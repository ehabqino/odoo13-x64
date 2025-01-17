# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.
from datetime import timedelta

from odoo import api, fields, models, tools
from odoo.addons.rating.models.rating import RATING_LIMIT_SATISFIED, RATING_LIMIT_OK
from odoo.osv import expression


class RatingParentMixin(models.AbstractModel):
    _name = 'rating.parent.mixin'
    _description = "Rating Parent Mixin"
    _rating_satisfaction_days = False  # Number of last days used to compute parent satisfaction. Set to False to include all existing rating.

    rating_ids = fields.One2many('rating.rating', 'parent_res_id', string='Ratings', domain=lambda self: [('parent_res_model', '=', self._name)], auto_join=True)
    rating_percentage_satisfaction = fields.Integer("Rating Satisfaction", compute="_compute_rating_percentage_satisfaction", store=False, help="Percentage of happy ratings")

    @api.depends('rating_ids.rating', 'rating_ids.consumed')
    def _compute_rating_percentage_satisfaction(self):
        # build domain and fetch data
        domain = [('parent_res_model', '=', self._name), ('parent_res_id', 'in', self.ids), ('rating', '>=', 1), ('consumed', '=', True)]
        if self._rating_satisfaction_days:
            domain += [('write_date', '>=', fields.Datetime.to_string(fields.datetime.now() - timedelta(days=self._rating_satisfaction_days)))]
        data = self.env['rating.rating'].read_group(domain, ['parent_res_id', 'rating'], ['parent_res_id', 'rating'], lazy=False)

        # get repartition of grades per parent id
        default_grades = {'great': 0, 'okay': 0, 'bad': 0}
        grades_per_parent = dict((parent_id, dict(default_grades)) for parent_id in self.ids)  # map: {parent_id: {'great': 0, 'bad': 0, 'ok': 0}}
        for item in data:
            parent_id = item['parent_res_id']
            rating = item['rating']
            if rating >= RATING_LIMIT_SATISFIED:
                grades_per_parent[parent_id]['great'] += item['__count']
            elif rating > RATING_LIMIT_OK:
                grades_per_parent[parent_id]['okay'] += item['__count']
            else:
                grades_per_parent[parent_id]['bad'] += item['__count']

        # compute percentage per parent
        for record in self:
            repartition = grades_per_parent.get(record.id)
            record.rating_percentage_satisfaction = repartition['great'] * 100 / sum(repartition.values()) if sum(repartition.values()) else -1


class RatingMixin(models.AbstractModel):
    _name = 'rating.mixin'
    _description = "Rating Mixin"

    rating_ids = fields.One2many('rating.rating', 'res_id', string='Rating', domain=lambda self: [('res_model', '=', self._name)], auto_join=True)
    rating_last_value = fields.Float('Rating Last Value', compute='_compute_rating_last_value', compute_sudo=True, store=True)
    rating_last_feedback = fields.Text('Rating Last Feedback', related='rating_ids.feedback', readonly=False)
    rating_last_image = fields.Binary('Rating Last Image', related='rating_ids.rating_image', readonly=False)
    rating_count = fields.Integer('Rating count', compute="_compute_rating_stats")
    rating_avg = fields.Float("Rating Average", compute='_compute_rating_stats')

    @api.depends('rating_ids.rating')
    def _compute_rating_last_value(self):
        for record in self:
            ratings = self.env['rating.rating'].search([('res_model', '=', self._name), ('res_id', '=', record.id)], limit=1)
            if ratings:
                record.rating_last_value = ratings.rating

    @api.depends('rating_ids')
    def _compute_rating_stats(self):
        """ Compute avg and count in one query, as thoses fields will be used together most of the time. """
        domain = self._rating_domain()
        read_group_res = self.env['rating.rating'].read_group(domain, ['rating:avg'], groupby=['res_id'], lazy=False)  # force average on rating column
        mapping = {item['res_id']: {'rating_count': item['__count'], 'rating_avg': item['rating']} for item in read_group_res}
        for record in self:
            record.rating_count = mapping.get(record.id, {}).get('rating_count', 0)
            record.rating_avg = mapping.get(record.id, {}).get('rating_avg', 0)

    def write(self, values):
        """ If the rated ressource name is modified, we should update the rating res_name too.
            If the rated ressource parent is changed we should update the parent_res_id too"""
        with self.env.norecompute():
            result = super(RatingMixin, self).write(values)
            for record in self:
                if record._rec_name in values:  # set the res_name of ratings to be recomputed
                    res_name_field = self.env['rating.rating']._fields['res_name']
                    record.rating_ids._recompute_todo(res_name_field)
                if record._rating_get_parent_field_name() in values:
                    record.rating_ids.write({'parent_res_id': record[record._rating_get_parent_field_name()].id})

        if self.env.recompute and self._context.get('recompute', True):  # trigger the recomputation of all field marked as "to recompute"
            self.recompute()

        return result

    def unlink(self):
        """ When removing a record, its rating should be deleted too. """
        record_ids = self.ids
        result = super(RatingMixin, self).unlink()
        self.env['rating.rating'].sudo().search([('res_model', '=', self._name), ('res_id', 'in', record_ids)]).unlink()
        return result

    def _rating_get_parent_field_name(self):
        """Return the parent relation field name
           Should return a Many2One"""
        return None

    def _rating_domain(self):
        """ Returns a normalized domain on rating.rating to select the records to
            include in count, avg, ... computation of current model.
        """
        return ['&', '&', ('res_model', '=', self._name), ('res_id', 'in', self.ids), ('consumed', '=', True)]

    def rating_get_partner_id(self):
        if hasattr(self, 'partner_id') and self.partner_id:
            return self.partner_id
        return self.env['res.partner']

    def rating_get_rated_partner_id(self):
        if hasattr(self, 'user_id') and self.user_id.partner_id:
            return self.user_id.partner_id
        return self.env['res.partner']

    def rating_get_access_token(self, partner=None):
        if not partner:
            partner = self.rating_get_partner_id()
        rated_partner = self.rating_get_rated_partner_id()
        ratings = self.rating_ids.filtered(lambda x: x.partner_id.id == partner.id and not x.consumed)
        if not ratings:
            record_model_id = self.env['ir.model'].sudo().search([('model', '=', self._name)], limit=1).id
            rating = self.env['rating.rating'].create({
                'partner_id': partner.id,
                'rated_partner_id': rated_partner.id,
                'res_model_id': record_model_id,
                'res_id': self.id
            })
        else:
            rating = ratings[0]
        return rating.access_token

    def rating_send_request(self, template, lang=False, subtype_id=False, force_send=True, composition_mode='comment', notif_layout=None):
        """ This method send rating request by email, using a template given
        in parameter.

         :param template: a mail.template record used to compute the message body;
         :param lang: optional lang; it can also be specified directly on the template
           itself in the lang field;
         :param subtype_id: optional subtype to use when creating the message; is
           a note by default to avoid spamming followers;
         :param force_send: whether to send the request directly or use the mail
           queue cron (preferred option);
         :param composition_mode: comment (message_post) or mass_mail (template.send_mail);
         :param notif_layout: layout used to encapsulate the content when sending email;
        """
        if lang:
            template = template.with_context(lang=lang)
        if subtype_id is False:
            subtype_id = self.env['ir.model.data'].xmlid_to_res_id('mail.mt_note')
        if force_send:
            self = self.with_context(mail_notify_force_send=True)  # default value is True, should be set to false if not?
        for record in self:
            record.message_post_with_template(
                template.id,
                composition_mode=composition_mode,
                email_layout_xmlid=notif_layout if notif_layout is not None else 'mail.mail_notification_light',
                subtype_id=subtype_id
            )

    def rating_apply(self, rate, token=None, feedback=None, subtype=None):
        """ Apply a rating given a token. If the current model inherits from
        mail.thread mixing, a message is posted on its chatter.
        :param rate : the rating value to apply
        :type rate : float
        :param token : access token
        :param feedback : additional feedback
        :type feedback : string
        :param subtype : subtype for mail
        :type subtype : string
        :returns rating.rating record
        """
        Rating, rating = self.env['rating.rating'], None
        if token:
            rating = self.env['rating.rating'].search([('access_token', '=', token)], limit=1)
        else:
            rating = Rating.search([('res_model', '=', self._name), ('res_id', '=', self.ids[0])], limit=1)
        if rating:
            rating.write({'rating': rate, 'feedback': feedback, 'consumed': True})
            if hasattr(self, 'message_post'):
                feedback = tools.plaintext2html(feedback or '')
                self.message_post(
                    body="<img src='/rating/static/src/img/rating_%s.png' alt=':%s/10' style='width:18px;height:18px;float:left;margin-right: 5px;'/>%s"
                    % (rate, rate, feedback),
                    subtype=subtype or "mail.mt_comment",
                    author_id=rating.partner_id and rating.partner_id.id or None  # None will set the default author in mail_thread.py
                )
            if hasattr(self, 'stage_id') and self.stage_id and hasattr(self.stage_id, 'auto_validation_kanban_state') and self.stage_id.auto_validation_kanban_state:
                if rating.rating > 5:
                    self.write({'kanban_state': 'done'})
                if rating.rating < 5:
                    self.write({'kanban_state': 'blocked'})
        return rating

    def rating_get_repartition(self, add_stats=False, domain=None):
        """ get the repatition of rating grade for the given res_ids.
            :param add_stats : flag to add stat to the result
            :type add_stats : boolean
            :param domain : optional extra domain of the rating to include/exclude in repartition
            :return dictionnary
                if not add_stats, the dict is like
                    - key is the rating value (integer)
                    - value is the number of object (res_model, res_id) having the value
                otherwise, key is the value of the information (string) : either stat name (avg, total, ...) or 'repartition'
                containing the same dict if add_stats was False.
        """
        base_domain = expression.AND([self._rating_domain(), [('rating', '>=', 1)]])
        if domain:
            base_domain += domain
        data = self.env['rating.rating'].read_group(base_domain, ['rating'], ['rating', 'res_id'])
        # init dict with all posible rate value, except 0 (no value for the rating)
        values = dict.fromkeys(range(1, 11), 0)
        values.update((d['rating'], d['rating_count']) for d in data)
        # add other stats
        if add_stats:
            rating_number = sum(values.values())
            result = {
                'repartition': values,
                'avg': sum(float(key * values[key]) for key in values) / rating_number if rating_number > 0 else 0,
                'total': sum(it['rating_count'] for it in data),
            }
            return result
        return values

    def rating_get_grades(self, domain=None):
        """ get the repatition of rating grade for the given res_ids.
            :param domain : optional domain of the rating to include/exclude in grades computation
            :return dictionnary where the key is the grade (great, okay, bad), and the value, the number of object (res_model, res_id) having the grade
                    the grade are compute as    0-30% : Bad
                                                31-69%: Okay
                                                70-100%: Great
        """
        data = self.rating_get_repartition(domain=domain)
        res = dict.fromkeys(['great', 'okay', 'bad'], 0)
        for key in data:
            if key >= RATING_LIMIT_SATISFIED:
                res['great'] += data[key]
            elif key > RATING_LIMIT_OK:
                res['okay'] += data[key]
            else:
                res['bad'] += data[key]
        return res

    def rating_get_stats(self, domain=None):
        """ get the statistics of the rating repatition
            :param domain : optional domain of the rating to include/exclude in statistic computation
            :return dictionnary where
                - key is the the name of the information (stat name)
                - value is statistic value : 'percent' contains the repartition in percentage, 'avg' is the average rate
                  and 'total' is the number of rating
        """
        data = self.rating_get_repartition(domain=domain, add_stats=True)
        result = {
            'avg': data['avg'],
            'total': data['total'],
            'percent': dict.fromkeys(range(1, 11), 0),
        }
        for rate in data['repartition']:
            result['percent'][rate] = (data['repartition'][rate] * 100) / data['total'] if data['total'] > 0 else 0
        return result
