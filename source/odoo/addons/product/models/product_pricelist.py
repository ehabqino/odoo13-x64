# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.

from itertools import chain

from odoo import api, fields, models, tools, _
from odoo.exceptions import UserError, ValidationError



class Pricelist(models.Model):
    _name = "product.pricelist"
    _description = "Pricelist"
    _order = "sequence asc, id desc"

    def _get_default_currency_id(self):
        return self.env.company.currency_id.id

    def _get_default_item_ids(self):
        ProductPricelistItem = self.env['product.pricelist.item']
        vals = ProductPricelistItem.default_get(list(ProductPricelistItem._fields))
        vals.update(compute_price='formula')
        return [[0, False, vals]]

    name = fields.Char('Pricelist Name', required=True, translate=True)
    active = fields.Boolean('Active', default=True, help="If unchecked, it will allow you to hide the pricelist without removing it.")
    item_ids = fields.One2many(
        'product.pricelist.item', 'pricelist_id', 'Pricelist Items',
        copy=True, default=_get_default_item_ids)
    currency_id = fields.Many2one('res.currency', 'Currency', default=_get_default_currency_id, required=True)
    company_id = fields.Many2one('res.company', 'Company')

    sequence = fields.Integer(default=16)
    country_group_ids = fields.Many2many('res.country.group', 'res_country_group_pricelist_rel',
                                         'pricelist_id', 'res_country_group_id', string='Country Groups')

    discount_policy = fields.Selection([
        ('with_discount', 'Discount included in the price'),
        ('without_discount', 'Show public price & discount to the customer')],
        default='with_discount')

    def name_get(self):
        return [(pricelist.id, '%s (%s)' % (pricelist.name, pricelist.currency_id.name)) for pricelist in self]

    @api.model
    def _name_search(self, name, args=None, operator='ilike', limit=100, name_get_uid=None):
        if name and operator == '=' and not args:
            # search on the name of the pricelist and its currency, opposite of name_get(),
            # Used by the magic context filter in the product search view.
            query_args = {'name': name, 'limit': limit, 'lang': self._context.get('lang') or 'en_US'}
            query = """SELECT p.id
                       FROM ((
                                SELECT pr.id, pr.name
                                FROM product_pricelist pr JOIN
                                     res_currency cur ON 
                                         (pr.currency_id = cur.id)
                                WHERE pr.name || ' (' || cur.name || ')' = %(name)s
                            )
                            UNION (
                                SELECT tr.res_id as id, tr.value as name
                                FROM ir_translation tr JOIN
                                     product_pricelist pr ON (
                                        pr.id = tr.res_id AND
                                        tr.type = 'model' AND
                                        tr.name = 'product.pricelist,name' AND
                                        tr.lang = %(lang)s
                                     ) JOIN
                                     res_currency cur ON 
                                         (pr.currency_id = cur.id)
                                WHERE tr.value || ' (' || cur.name || ')' = %(name)s
                            )
                        ) p
                       ORDER BY p.name"""
            if limit:
                query += " LIMIT %(limit)s"
            self._cr.execute(query, query_args)
            ids = [r[0] for r in self._cr.fetchall()]
            # regular search() to apply ACLs - may limit results below limit in some cases
            pricelist_ids = self._search([('id', 'in', ids)], limit=limit, access_rights_uid=name_get_uid)
            if pricelist_ids:
                return self.browse(pricelist_ids).name_get()
        return super(Pricelist, self)._name_search(name, args, operator=operator, limit=limit, name_get_uid=name_get_uid)

    def _compute_price_rule_multi(self, products_qty_partner, date=False, uom_id=False):
        """ Low-level method - Multi pricelist, multi products
        Returns: dict{product_id: dict{pricelist_id: (price, suitable_rule)} }"""
        if not self.ids:
            pricelists = self.search([])
        else:
            pricelists = self
        results = {}
        for pricelist in pricelists:
            subres = pricelist._compute_price_rule(products_qty_partner, date=date, uom_id=uom_id)
            for product_id, price in subres.items():
                results.setdefault(product_id, {})
                results[product_id][pricelist.id] = price
        return results

    def _compute_price_rule_get_items(self, products_qty_partner, date, uom_id, prod_tmpl_ids, prod_ids, categ_ids):
        self.ensure_one()
        # Load all rules
        self.env.cr.execute(
            """
            SELECT
                item.id
            FROM
                product_pricelist_item AS item
            LEFT JOIN product_category AS categ ON item.categ_id = categ.id
            WHERE
                (item.product_tmpl_id IS NULL OR item.product_tmpl_id = any(%s))
                AND (item.product_id IS NULL OR item.product_id = any(%s))
                AND (item.categ_id IS NULL OR item.categ_id = any(%s))
                AND (item.pricelist_id = %s)
                AND (item.date_start IS NULL OR item.date_start<=%s)
                AND (item.date_end IS NULL OR item.date_end>=%s)
            ORDER BY
                item.applied_on, item.min_quantity desc, categ.complete_name desc, item.id desc
            """,
            (prod_tmpl_ids, prod_ids, categ_ids, self.id, date, date))
        # NOTE: if you change `order by` on that query, make sure it matches
        # _order from model to avoid inconstencies and undeterministic issues.

        item_ids = [x[0] for x in self.env.cr.fetchall()]
        return self.env['product.pricelist.item'].browse(item_ids)

    def _compute_price_rule(self, products_qty_partner, date=False, uom_id=False):
        """ Low-level method - Mono pricelist, multi products
        Returns: dict{product_id: (price, suitable_rule) for the given pricelist}

        If date in context: Date of the pricelist (%Y-%m-%d)

            :param products_qty_partner: list of typles products, quantity, partner
            :param datetime date: validity date
            :param ID uom_id: intermediate unit of measure
        """
        self.ensure_one()
        if not date:
            date = self._context.get('date') or fields.Date.context_today(self)
        if not uom_id and self._context.get('uom'):
            uom_id = self._context['uom']
        if uom_id:
            # rebrowse with uom if given
            products = [item[0].with_context(uom=uom_id) for item in products_qty_partner]
            products_qty_partner = [(products[index], data_struct[1], data_struct[2]) for index, data_struct in enumerate(products_qty_partner)]
        else:
            products = [item[0] for item in products_qty_partner]

        if not products:
            return {}

        categ_ids = {}
        for p in products:
            categ = p.categ_id
            while categ:
                categ_ids[categ.id] = True
                categ = categ.parent_id
        categ_ids = list(categ_ids)

        is_product_template = products[0]._name == "product.template"
        if is_product_template:
            prod_tmpl_ids = [tmpl.id for tmpl in products]
            # all variants of all products
            prod_ids = [p.id for p in
                        list(chain.from_iterable([t.product_variant_ids for t in products]))]
        else:
            prod_ids = [product.id for product in products]
            prod_tmpl_ids = [product.product_tmpl_id.id for product in products]

        items = self._compute_price_rule_get_items(products_qty_partner, date, uom_id, prod_tmpl_ids, prod_ids, categ_ids)

        results = {}
        for product, qty, partner in products_qty_partner:
            results[product.id] = 0.0
            suitable_rule = False

            # Final unit price is computed according to `qty` in the `qty_uom_id` UoM.
            # An intermediary unit price may be computed according to a different UoM, in
            # which case the price_uom_id contains that UoM.
            # The final price will be converted to match `qty_uom_id`.
            qty_uom_id = self._context.get('uom') or product.uom_id.id
            price_uom_id = product.uom_id.id
            qty_in_product_uom = qty
            if qty_uom_id != product.uom_id.id:
                try:
                    qty_in_product_uom = self.env['uom.uom'].browse([self._context['uom']])._compute_quantity(qty, product.uom_id)
                except UserError:
                    # Ignored - incompatible UoM in context, use default product UoM
                    pass

            # if Public user try to access standard price from website sale, need to call price_compute.
            # TDE SURPRISE: product can actually be a template
            price = product.price_compute('list_price')[product.id]

            price_uom = self.env['uom.uom'].browse([qty_uom_id])
            for rule in items:
                if rule.min_quantity and qty_in_product_uom < rule.min_quantity:
                    continue
                if is_product_template:
                    if rule.product_tmpl_id and product.id != rule.product_tmpl_id.id:
                        continue
                    if rule.product_id and not (product.product_variant_count == 1 and product.product_variant_id.id == rule.product_id.id):
                        # product rule acceptable on template if has only one variant
                        continue
                else:
                    if rule.product_tmpl_id and product.product_tmpl_id.id != rule.product_tmpl_id.id:
                        continue
                    if rule.product_id and product.id != rule.product_id.id:
                        continue

                if rule.categ_id:
                    cat = product.categ_id
                    while cat:
                        if cat.id == rule.categ_id.id:
                            break
                        cat = cat.parent_id
                    if not cat:
                        continue

                if rule.base == 'pricelist' and rule.base_pricelist_id:
                    price_tmp = rule.base_pricelist_id._compute_price_rule([(product, qty, partner)])[product.id][0]  # TDE: 0 = price, 1 = rule
                    price = rule.base_pricelist_id.currency_id._convert(price_tmp, self.currency_id, self.env.company, date, round=False)
                else:
                    # if base option is public price take sale price else cost price of product
                    # price_compute returns the price in the context UoM, i.e. qty_uom_id
                    price = product.price_compute(rule.base)[product.id]

                convert_to_price_uom = (lambda price: product.uom_id._compute_price(price, price_uom))

                if price is not False:
                    if rule.compute_price == 'fixed':
                        price = convert_to_price_uom(rule.fixed_price)
                    elif rule.compute_price == 'percentage':
                        price = (price - (price * (rule.percent_price / 100))) or 0.0
                    else:
                        # complete formula
                        price_limit = price
                        price = (price - (price * (rule.price_discount / 100))) or 0.0
                        if rule.price_round:
                            price = tools.float_round(price, precision_rounding=rule.price_round)

                        if rule.price_surcharge:
                            price_surcharge = convert_to_price_uom(rule.price_surcharge)
                            price += price_surcharge

                        if rule.price_min_margin:
                            price_min_margin = convert_to_price_uom(rule.price_min_margin)
                            price = max(price, price_limit + price_min_margin)

                        if rule.price_max_margin:
                            price_max_margin = convert_to_price_uom(rule.price_max_margin)
                            price = min(price, price_limit + price_max_margin)
                    suitable_rule = rule
                break
            # Final price conversion into pricelist currency
            if suitable_rule and suitable_rule.compute_price != 'fixed' and suitable_rule.base != 'pricelist':
                if suitable_rule.base == 'standard_price':
                    cur = product.cost_currency_id
                else:
                    cur = product.currency_id
                price = cur._convert(price, self.currency_id, self.env.company, date, round=False)

            results[product.id] = (price, suitable_rule and suitable_rule.id or False)

        return results

    # New methods: product based
    def get_products_price(self, products, quantities, partners, date=False, uom_id=False):
        """ For a given pricelist, return price for products
        Returns: dict{product_id: product price}, in the given pricelist """
        self.ensure_one()
        return {
            product_id: res_tuple[0]
            for product_id, res_tuple in self._compute_price_rule(
                list(zip(products, quantities, partners)),
                date=date,
                uom_id=uom_id
            ).items()
        }

    def get_product_price(self, product, quantity, partner, date=False, uom_id=False):
        """ For a given pricelist, return price for a given product """
        self.ensure_one()
        return self._compute_price_rule([(product, quantity, partner)], date=date, uom_id=uom_id)[product.id][0]

    def get_product_price_rule(self, product, quantity, partner, date=False, uom_id=False):
        """ For a given pricelist, return price and rule for a given product """
        self.ensure_one()
        return self._compute_price_rule([(product, quantity, partner)], date=date, uom_id=uom_id)[product.id]

    def price_get(self, prod_id, qty, partner=None):
        """ Multi pricelist, mono product - returns price per pricelist """
        return {key: price[0] for key, price in self.price_rule_get(prod_id, qty, partner=partner).items()}

    def price_rule_get_multi(self, products_by_qty_by_partner):
        """ Multi pricelist, multi product  - return tuple """
        return self._compute_price_rule_multi(products_by_qty_by_partner)

    def price_rule_get(self, prod_id, qty, partner=None):
        """ Multi pricelist, mono product - return tuple """
        product = self.env['product.product'].browse([prod_id])
        return self._compute_price_rule_multi([(product, qty, partner)])[prod_id]

    @api.model
    def _price_get_multi(self, pricelist, products_by_qty_by_partner):
        """ Mono pricelist, multi product - return price per product """
        return pricelist.get_products_price(
            list(zip(**products_by_qty_by_partner)))

    def _get_partner_pricelist_multi_search_domain_hook(self):
        return []

    def _get_partner_pricelist_multi_filter_hook(self):
        return self

    def _get_partner_pricelist_multi(self, partner_ids, company_id=None):
        """ Retrieve the applicable pricelist for given partners in a given company.

            It will return the first found pricelist in this order:
            First, the pricelist of the specific property (res_id set), this one
                   is created when saving a pricelist on the partner form view.
            Else, it will return the pricelist of the partner country group
            Else, it will return the generic property (res_id not set), this one
                  is created on the company creation.
            Else, it will return the first available pricelist

            :param company_id: if passed, used for looking up properties,
                instead of current user's company
            :return: a dict {partner_id: pricelist}
        """
        # `partner_ids` might be ID from inactive uers. We should use active_test
        # as we will do a search() later (real case for website public user).
        Partner = self.env['res.partner'].with_context(active_test=False)

        Property = self.env['ir.property'].with_context(force_company=company_id or self.env.company.id)
        Pricelist = self.env['product.pricelist']
        pl_domain = self._get_partner_pricelist_multi_search_domain_hook()

        # if no specific property, try to find a fitting pricelist
        result = Property.get_multi('property_product_pricelist', Partner._name, partner_ids)

        remaining_partner_ids = [pid for pid, val in result.items() if not val or
                                 not val._get_partner_pricelist_multi_filter_hook()]
        if remaining_partner_ids:
            # get fallback pricelist when no pricelist for a given country
            pl_fallback = (
                Pricelist.search(pl_domain + [('country_group_ids', '=', False)], limit=1) or
                Property.get('property_product_pricelist', 'res.partner') or
                Pricelist.search(pl_domain, limit=1)
            )
            # group partners by country, and find a pricelist for each country
            domain = [('id', 'in', remaining_partner_ids)]
            groups = Partner.read_group(domain, ['country_id'], ['country_id'])
            for group in groups:
                country_id = group['country_id'] and group['country_id'][0]
                pl = Pricelist.search(pl_domain + [('country_group_ids.country_ids', '=', country_id)], limit=1)
                pl = pl or pl_fallback
                for pid in Partner.search(group['__domain']).ids:
                    result[pid] = pl

        return result

    @api.model
    def get_import_templates(self):
        return [{
            'label': _('Import Template for Pricelists'),
            'template': '/product/static/xls/product_pricelist.xls'
        }]


class ResCountryGroup(models.Model):
    _inherit = 'res.country.group'

    pricelist_ids = fields.Many2many('product.pricelist', 'res_country_group_pricelist_rel',
                                     'res_country_group_id', 'pricelist_id', string='Pricelists')


class PricelistItem(models.Model):
    _name = "product.pricelist.item"
    _description = "Pricelist Item"
    _order = "applied_on, min_quantity desc, categ_id desc, id desc"
    # NOTE: if you change _order on this model, make sure it matches the SQL
    # query built in _compute_price_rule() above in this file to avoid
    # inconstencies and undeterministic issues.

    def _get_default_applied_on(self):
        return self.env.user.has_group('product.group_product_pricelist') and '1_product' or '3_global'

    product_tmpl_id = fields.Many2one(
        'product.template', 'Product Template', ondelete='cascade',
        help="Specify a template if this rule only applies to one product template. Keep empty otherwise.")
    product_id = fields.Many2one(
        'product.product', 'Product', ondelete='cascade',
        help="Specify a product if this rule only applies to one product. Keep empty otherwise.")
    categ_id = fields.Many2one(
        'product.category', 'Product Category', ondelete='cascade',
        help="Specify a product category if this rule only applies to products belonging to this category or its children categories. Keep empty otherwise.")
    min_quantity = fields.Integer(
        'Min. Quantity', default=0,
        help="For the rule to apply, bought/sold quantity must be greater "
             "than or equal to the minimum quantity specified in this field.\n"
             "Expressed in the default unit of measure of the product.")
    applied_on = fields.Selection([
        ('3_global', 'Global'),
        ('2_product_category', ' Product Category'),
        ('1_product', 'Product'),
        ('0_product_variant', 'Product Variant')], "Apply On",
        default=_get_default_applied_on, required=True,
        help='Pricelist Item applicable on selected option')
    base = fields.Selection([
        ('list_price', 'Sales Price'),
        ('standard_price', 'Cost'),
        ('pricelist', 'Other Pricelist')], "Based on",
        default='list_price', required=True,
        help='Base price for computation.\n'
             'Sales Price: The base price will be the Sales Price.\n'
             'Cost Price : The base price will be the cost price.\n'
             'Other Pricelist : Computation of the base price based on another Pricelist.')
    base_pricelist_id = fields.Many2one('product.pricelist', 'Other Pricelist')
    pricelist_id = fields.Many2one('product.pricelist', 'Pricelist', index=True, ondelete='cascade')
    price_surcharge = fields.Float(
        'Price Surcharge', digits='Product Price',
        help='Specify the fixed amount to add or substract(if negative) to the amount calculated with the discount.')
    price_discount = fields.Float('Price Discount', default=0, digits=(16, 2))
    price_round = fields.Float(
        'Price Rounding', digits='Product Price',
        help="Sets the price so that it is a multiple of this value.\n"
             "Rounding is applied after the discount and before the surcharge.\n"
             "To have prices that end in 9.99, set rounding 10, surcharge -0.01")
    price_min_margin = fields.Float(
        'Min. Price Margin', digits='Product Price',
        help='Specify the minimum amount of margin over the base price.')
    price_max_margin = fields.Float(
        'Max. Price Margin', digits='Product Price',
        help='Specify the maximum amount of margin over the base price.')
    company_id = fields.Many2one(
        'res.company', 'Company',
        readonly=True, related='pricelist_id.company_id', store=True)
    currency_id = fields.Many2one(
        'res.currency', 'Currency',
        readonly=True, related='pricelist_id.currency_id', store=True)
    date_start = fields.Date('Start Date', help="Starting date for the pricelist item validation")
    date_end = fields.Date('End Date', help="Ending valid for the pricelist item validation")
    compute_price = fields.Selection([
        ('fixed', 'Fix Price'),
        ('percentage', 'Percentage (discount)'),
        ('formula', 'Formula')], index=True, default='fixed')
    fixed_price = fields.Float('Fixed Price', digits='Product Price')
    percent_price = fields.Float('Percentage Price')
    # functional fields used for usability purposes
    name = fields.Char(
        'Name', compute='_get_pricelist_item_name_price',
        help="Explicit rule name for this pricelist line.")
    price = fields.Char(
        'Price', compute='_get_pricelist_item_name_price',
        help="Explicit rule name for this pricelist line.")

    @api.constrains('base_pricelist_id', 'pricelist_id', 'base')
    def _check_recursion(self):
        if any(item.base == 'pricelist' and item.pricelist_id and item.pricelist_id == item.base_pricelist_id for item in self):
            raise ValidationError(_('You cannot assign the Main Pricelist as Other Pricelist in PriceList Item'))
        return True

    @api.constrains('price_min_margin', 'price_max_margin')
    def _check_margin(self):
        if any(item.price_min_margin > item.price_max_margin for item in self):
            raise ValidationError(_('The minimum margin should be lower than the maximum margin.'))
        return True

    @api.depends('categ_id', 'product_tmpl_id', 'product_id', 'compute_price', 'fixed_price', \
        'pricelist_id', 'percent_price', 'price_discount', 'price_surcharge')
    def _get_pricelist_item_name_price(self):
        for item in self:
            if item.categ_id:
                item.name = _("Category: %s") % (item.categ_id.name)
            elif item.product_tmpl_id:
                item.name = item.product_tmpl_id.name
            elif item.product_id:
                item.name = item.product_id.display_name.replace('[%s]' % item.product_id.code, '')
            else:
                item.name = _("All Products")

            if item.compute_price == 'fixed':
                item.price = ("%s %s") % (item.fixed_price, item.pricelist_id.currency_id.name)
            elif item.compute_price == 'percentage':
                item.price = _("%s %% discount") % (item.percent_price)
            else:
                item.price = _("%s %% discount and %s surcharge") % (item.price_discount, item.price_surcharge)

    @api.onchange('applied_on')
    def _onchange_applied_on(self):
        if self.applied_on != '0_product_variant':
            self.product_id = False
        if self.applied_on != '1_product':
            self.product_tmpl_id = False
        if self.applied_on != '2_product_category':
            self.categ_id = False

    @api.onchange('compute_price')
    def _onchange_compute_price(self):
        if self.compute_price != 'fixed':
            self.fixed_price = 0.0
        if self.compute_price != 'percentage':
            self.percent_price = 0.0
        if self.compute_price != 'formula':
            self.update({
                'price_discount': 0.0,
                'price_surcharge': 0.0,
                'price_round': 0.0,
                'price_min_margin': 0.0,
                'price_max_margin': 0.0,
            })

    def write(self, values):
        res = super(PricelistItem, self).write(values)
        # When the pricelist changes we need the product.template price
        # to be invalided and recomputed.
        self.invalidate_cache()
        return res

    @api.onchange('product_tmpl_id')
    def _onchange_product_tmpl_id(self):
        if self.user_has_groups('product.group_product_variant'):
            self.product_id = False
