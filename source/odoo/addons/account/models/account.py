# -*- coding: utf-8 -*-

import time
import math
import re

from odoo.osv import expression
from odoo.tools.float_utils import float_round as round, float_compare
from odoo.tools import DEFAULT_SERVER_DATETIME_FORMAT
from odoo.exceptions import UserError, ValidationError
from odoo import api, fields, models, _


class AccountAccountType(models.Model):
    _name = "account.account.type"
    _description = "Account Type"

    name = fields.Char(string='Account Type', required=True, translate=True)
    include_initial_balance = fields.Boolean(string="Bring Accounts Balance Forward", help="Used in reports to know if we should consider journal items from the beginning of time instead of from the fiscal year only. Account types that should be reset to zero at each new fiscal year (like expenses, revenue..) should not have this option set.")
    type = fields.Selection([
        ('other', 'Regular'),
        ('receivable', 'Receivable'),
        ('payable', 'Payable'),
        ('liquidity', 'Liquidity'),
    ], required=True, default='other',
        help="The 'Internal Type' is used for features available on "\
        "different types of accounts: liquidity type is for cash or bank accounts"\
        ", payable/receivable is for vendor/customer accounts.")
    internal_group = fields.Selection([
        ('equity', 'Equity'),
        ('asset', 'Asset'),
        ('liability', 'Liability'),
        ('income', 'Income'),
        ('expense', 'Expense'),
    ], string="Internal Group",
       help="The 'Internal Group' is used to filter accounts based on the internal group set on the account type.")
    note = fields.Text(string='Description')


class AccountAccountTag(models.Model):
    _name = 'account.account.tag'
    _description = 'Account Tag'

    name = fields.Char('Tag Name', required=True)
    applicability = fields.Selection([('accounts', 'Accounts'), ('taxes', 'Taxes')], required=True, default='accounts')
    color = fields.Integer('Color Index')
    active = fields.Boolean(default=True, help="Set active to false to hide the Account Tag without removing it.")
    tax_report_line_ids = fields.Many2many(string="Tax Report Lines", comodel_name='account.tax.report.line', relation='account_tax_report_line_tags_rel', help="The tax report lines using this tag")
    tax_negate = fields.Boolean(string="Negate Tax Balance", help="Check this boox to negate the absolute value of the balance of the lines associated with this tag in tax report computation.")
    country_id = fields.Many2one(string="Country", comodel_name='res.country', help="Country for which this tag is available, when applied on taxes.")


class AccountTaxReportLine(models.Model):
    _name = "account.tax.report.line"
    _description = 'Account Tax Report Line'
    _order = 'sequence'
    _parent_store = True

    name = fields.Char(string="Name", required=True, help="Complete name for this report line, to be used in report.")
    tag_ids = fields.Many2many(string="Tags", comodel_name='account.account.tag', relation='account_tax_report_line_tags_rel', help="Tax tags populating this line")
    country_id = fields.Many2one(string="Country", comodel_name='res.country', required=True, default=lambda x: x.env.user.company_id.country_id.id, help="Country for which this line is available.")
    report_action_id = fields.Many2one(string="Report Action", comodel_name='ir.actions.act_window', help="The optional action to call when clicking on this line in accounting reports.")
    children_line_ids = fields.One2many(string="Children Lines", comodel_name='account.tax.report.line', inverse_name='parent_id', help="Lines that should be rendered as children of this one")
    parent_id = fields.Many2one(string="Parent Line", comodel_name='account.tax.report.line')
    sequence = fields.Integer(string='Sequence', required=True, help="Sequence determining the order of the lines in the report (smaller ones come first). This order is applied locally per section (so, chilldren of the same line are always rendered one after the other).")
    parent_path = fields.Char(index=True)

    #helper to create tags (positive and negative) on report line creation
    tag_name = fields.Char(string="Tag Name", help="Short name for the tax grid corresponding to this report line. Leave empty if this report line should not correspond to any such grid.")

    #fields used in specific localization reports, where a report line isn't simply the given by the sum of account.move.line with selected tags
    code = fields.Char(string="Code", help="Optional unique code to refer to this line in total formulas")
    formula = fields.Char(string="Formula", help="Python expression used to compute the value of a total line. This field is mutually exclusive with tag_name, setting it turns the line to a total line. Tax report line codes can be used as variables in this expression to refer to the balance of the corresponding lines in the report. A formula cannot refer to another line using a formula.")

    @api.model
    def create(self, vals):
        tag_name = vals.get('tag_name', '')
        if tag_name:
            vals['tag_ids'] = self._get_tags_create_vals(tag_name, vals.get('country_id'))
        return super(AccountTaxReportLine, self).create(vals)

    @api.model
    def _get_tags_create_vals(self, tag_name, country_id):
        minus_tag_vals = {
          'name': '-' + tag_name,
          'applicability': 'taxes',
          'tax_negate': True,
          'country_id': country_id,
        }
        plus_tag_vals = {
          'name': '+' + tag_name,
          'applicability': 'taxes',
          'tax_negate': False,
          'country_id': country_id,
        }
        return [(0, 0, minus_tag_vals), (0, 0, plus_tag_vals)]

    def write(self, vals):
        rslt = super(AccountTaxReportLine, self).write(vals)
        if 'tag_name' in vals:
            # Constraint _validate_tags ensures that tag_ids always contains two
            # tags if not empty: + and -.
            # So, we can impact the whole tag_ids set if tag_name is modified.
            if vals['tag_name']:
                # If some lines in the set did not have tags yet, we create them
                without_tags = self.filtered(lambda x: not x.tag_ids)
                for record in without_tags:
                    record.write({'tag_ids': self._get_tags_create_vals(record.tag_name, record.country_id.id)})

                # We update the name of every tag linked to other lines
                to_update = self - without_tags
                tags_to_update = to_update.mapped('tag_ids')
                minus_child_tags = tags_to_update.filtered(lambda x: x.tax_negate)
                minus_child_tags.write({'name': '-' + vals['tag_name']})

                plus_child_tags = tags_to_update.filtered(lambda x: not x.tax_negate)
                plus_child_tags.write({'name': '+' + vals['tag_name']})

            else:
                # tag_name was set empty, so we remove the tags
                self._delete_tags_from_taxes(self.mapped('tag_ids.id'))
                self.write({'tag_ids': [(2, tag.id, 0) for tag in self.mapped('tag_ids')]})

        return rslt

    def unlink(self):
        self._delete_tags_from_taxes(self.mapped('tag_ids.id'))
        children = self.mapped('children_line_ids')
        if children:
            children.unlink()
        return super(AccountTaxReportLine, self).unlink()

    def _delete_tags_from_taxes(self, tag_ids_to_delete):
        """ Based on a list of tag ids, delete them all from database, removing them
        first from the repartition lines they are linked to.
        """
        repartition_lines = self.env['account.tax.repartition.line'].search([('tag_ids', 'in', tag_ids_to_delete)])
        repartition_lines.write({'tag_ids': [(3, tag_id, 0) for tag_id in tag_ids_to_delete]})

    @api.constrains('formula', 'tag_name')
    def _validate_formula(self):
        if self.formula and self.tag_name:
            raise ValidationError(_("Tag name and formula are mutually exclusive, they should not be set together on the same tax report line."))

    @api.constrains('tag_name', 'tag_ids')
    def _validate_tags(self):
        neg_tags = self.tag_ids.filtered(lambda x: x.tax_negate)
        pos_tags = self.tag_ids.filtered(lambda x: not x.tax_negate)
        if self.tag_ids and (len(neg_tags) !=1 or len(pos_tags) != 1):
            raise ValidationError(_("If tags are defined for a tax report line, only two are allowed on it: a positive and a negative one."))

    @api.onchange('parent_id')
    def _onchange_parent_id(self):
        """ If the parent of a report line is changed, the sequence of this line
        must be updated, in order for it to match  the sequence numbering of its
        parent. By default, we add it as the last line of this section.
        """
        if self.country_id:
            lines_domain = [('country_id', '=', self.country_id.id)]
            if self.parent_id:
                lines_domain.append(('parent_id', '=', self.parent_id.id))

            last_line = self.env['account.tax.report.line'].search(lines_domain, order="sequence desc", limit=1)
            self.sequence = last_line.sequence + 1

#----------------------------------------------------------
# Accounts
#----------------------------------------------------------


class AccountAccount(models.Model):
    _name = "account.account"
    _description = "Account"
    _order = "code"

    @api.constrains('internal_type', 'reconcile')
    def _check_reconcile(self):
        for account in self:
            if account.internal_type in ('receivable', 'payable') and account.reconcile == False:
                raise ValidationError(_('You cannot have a receivable/payable account that is not reconcilable. (account code: %s)') % account.code)

    @api.constrains('user_type_id')
    def _check_user_type_id(self):
        data_unaffected_earnings = self.env.ref('account.data_unaffected_earnings')
        result = self.read_group([('user_type_id', '=', data_unaffected_earnings.id)], ['company_id'], ['company_id'])
        for res in result:
            if res.get('company_id_count', 0) >= 2:
                account_unaffected_earnings = self.search([('company_id', '=', res['company_id'][0]),
                                                           ('user_type_id', '=', data_unaffected_earnings.id)])
                raise ValidationError(_('You cannot have more than one account with "Current Year Earnings" as type. (accounts: %s)') % [a.code for a in account_unaffected_earnings])

    name = fields.Char(required=True, index=True)
    currency_id = fields.Many2one('res.currency', string='Account Currency',
        help="Forces all moves for this account to have this account currency.")
    code = fields.Char(size=64, required=True, index=True)
    deprecated = fields.Boolean(index=True, default=False)
    user_type_id = fields.Many2one('account.account.type', string='Type', required=True, oldname="user_type",
        help="Account Type is used for information purpose, to generate country-specific legal reports, and set the rules to close a fiscal year and generate opening entries.")
    internal_type = fields.Selection(related='user_type_id.type', string="Internal Type", store=True, readonly=True)
    internal_group = fields.Selection(related='user_type_id.internal_group', string="Internal Group", store=True, readonly=True)
    #has_unreconciled_entries = fields.Boolean(compute='_compute_has_unreconciled_entries',
    #    help="The account has at least one unreconciled debit and credit since last time the invoices & payments matching was performed.")
    reconcile = fields.Boolean(string='Allow Reconciliation', default=False,
        help="Check this box if this account allows invoices & payments matching of journal items.")
    tax_ids = fields.Many2many('account.tax', 'account_account_tax_default_rel',
        'account_id', 'tax_id', string='Default Taxes', context={'append_type_to_tax_name': True})
    note = fields.Text('Internal Notes')
    company_id = fields.Many2one('res.company', string='Company', required=True,
        default=lambda self: self.env.company)
    tag_ids = fields.Many2many('account.account.tag', 'account_account_account_tag', string='Tags', help="Optional tags you may want to assign for custom reporting")
    group_id = fields.Many2one('account.group')

    opening_debit = fields.Monetary(string="Opening debit", compute='_compute_opening_debit_credit', inverse='_set_opening_debit', help="Opening debit value for this account.")
    opening_credit = fields.Monetary(string="Opening credit", compute='_compute_opening_debit_credit', inverse='_set_opening_credit', help="Opening credit value for this account.")

    _sql_constraints = [
        ('code_company_uniq', 'unique (code,company_id)', 'The code of the account must be unique per company !')
    ]

    @api.model
    def _search_new_account_code(self, company, digits, prefix):
        for num in range(1, 10000):
            new_code = str(prefix.ljust(digits - 1, '0')) + str(num)
            rec = self.search([('code', '=', new_code), ('company_id', '=', company.id)], limit=1)
            if not rec:
                return new_code
        raise UserError(_('Cannot generate an unused account code.'))

    def _compute_opening_debit_credit(self):
        for record in self:
            opening_debit = opening_credit = 0.0
            if record.company_id.account_opening_move_id:
                for line in self.env['account.move.line'].search([('account_id', '=', record.id),
                                                                 ('move_id','=', record.company_id.account_opening_move_id.id)]):
                    #could be executed at most twice: once for credit, once for debit
                    if line.debit:
                        opening_debit = line.debit
                    elif line.credit:
                        opening_credit = line.credit
            record.opening_debit = opening_debit
            record.opening_credit = opening_credit

    def _set_opening_debit(self):
        self._set_opening_debit_credit(self.opening_debit, 'debit')

    def _set_opening_credit(self):
        self._set_opening_debit_credit(self.opening_credit, 'credit')

    def _set_opening_debit_credit(self, amount, field):
        """ Generic function called by both opening_debit and opening_credit's
        inverse function. 'Amount' parameter is the value to be set, and field
        either 'debit' or 'credit', depending on which one of these two fields
        got assigned.
        """
        opening_move = self.company_id.account_opening_move_id

        if not opening_move:
            raise UserError(_("You must first define an opening move."))

        if opening_move.state == 'draft':
            # check whether we should create a new move line or modify an existing one
            opening_move_line = self.env['account.move.line'].search([('account_id', '=', self.id),
                                                                      ('move_id','=', opening_move.id),
                                                                      (field,'!=', False),
                                                                      (field,'!=', 0.0)]) # 0.0 condition important for import

            counter_part_map = {'debit': opening_move_line.credit, 'credit': opening_move_line.debit}
            # No typo here! We want the credit value when treating debit and debit value when treating credit

            if opening_move_line:
                if amount:
                    # modify the line
                    setattr(opening_move_line.with_context({'check_move_validity': False}), field, amount)
                elif counter_part_map[field]:
                    # delete the line (no need to keep a line with value = 0)
                    opening_move_line.with_context({'check_move_validity': False}).unlink()
            elif amount:
                # create a new line, as none existed before
                self.env['account.move.line'].with_context({'check_move_validity': False}).create({
                        'name': _('Opening balance'),
                        field: amount,
                        'move_id': opening_move.id,
                        'account_id': self.id,
                })

            # Then, we automatically balance the opening move, to make sure it stays valid
            if not 'import_file' in self.env.context:
                # When importing a file, avoid recomputing the opening move for each account and do it at the end, for better performances
                self.company_id._auto_balance_opening_move()

    @api.model
    def default_get(self, default_fields):
        """If we're creating a new account through a many2one, there are chances that we typed the account code
        instead of its name. In that case, switch both fields values.
        """
        default_name = self._context.get('default_name')
        default_code = self._context.get('default_code')
        if default_name and not default_code:
            try:
                default_code = int(default_name)
            except ValueError:
                pass
            if default_code:
                default_name = False
        contextual_self = self.with_context(default_name=default_name, default_code=default_code)
        return super(AccountAccount, contextual_self).default_get(default_fields)

    @api.model
    def _name_search(self, name, args=None, operator='ilike', limit=100, name_get_uid=None):
        args = args or []
        domain = []
        if name:
            domain = ['|', ('code', '=ilike', name.split(' ')[0] + '%'), ('name', operator, name)]
            if operator in expression.NEGATIVE_TERM_OPERATORS:
                domain = ['&', '!'] + domain[1:]
        account_ids = self._search(expression.AND([domain, args]), limit=limit, access_rights_uid=name_get_uid)
        return self.browse(account_ids).name_get()

    @api.onchange('internal_type')
    def onchange_internal_type(self):
        self.reconcile = self.internal_type in ('receivable', 'payable')
        if self.internal_type == 'liquidity':
            self.reconcile = False

    @api.onchange('code')
    def onchange_code(self):
        AccountGroup = self.env['account.group']

        group = False
        code_prefix = self.code

        # find group with longest matching prefix
        while code_prefix:
            matching_group = AccountGroup.search([('code_prefix', '=', code_prefix)], limit=1)
            if matching_group:
                group = matching_group
                break
            code_prefix = code_prefix[:-1]
        self.group_id = group

    def name_get(self):
        result = []
        for account in self:
            name = account.code + ' ' + account.name
            result.append((account.id, name))
        return result

    @api.returns('self', lambda value: value.id)
    def copy(self, default=None):
        default = dict(default or {})
        if default.get('code', False):
            return super(AccountAccount, self).copy(default)
        try:
            default['code'] = (str(int(self.code) + 10) or '')
            default.setdefault('name', _("%s (copy)") % (self.name or ''))
            while self.env['account.account'].search([('code', '=', default['code']),
                                                      ('company_id', '=', default.get('company_id', False) or self.company_id.id)], limit=1):
                default['code'] = (str(int(default['code']) + 10) or '')
                default['name'] = _("%s (copy)") % (self.name or '')
        except ValueError:
            default['code'] = _("%s (copy)") % (self.code or '')
            default['name'] = self.name
        return super(AccountAccount, self).copy(default)

    @api.model
    def load(self, fields, data):
        """ Overridden for better performances when importing a list of account
        with opening debit/credit. In that case, the auto-balance is postpone
        until the whole file has been imported.
        """
        rslt = super(AccountAccount, self).load(fields, data)

        if 'import_file' in self.env.context:
            companies = self.search([('id', 'in', rslt['ids'])]).mapped('company_id')
            for company in companies:
                company._auto_balance_opening_move()
        return rslt

    def _toggle_reconcile_to_true(self):
        '''Toggle the `reconcile´ boolean from False -> True

        Note that: lines with debit = credit = amount_currency = 0 are set to `reconciled´ = True
        '''
        if not self.ids:
            return None
        query = """
            UPDATE account_move_line SET
                reconciled = CASE WHEN debit = 0 AND credit = 0 AND amount_currency = 0
                    THEN true ELSE false END,
                amount_residual = (debit-credit),
                amount_residual_currency = amount_currency
            WHERE full_reconcile_id IS NULL and account_id IN %s
        """
        self.env.cr.execute(query, [tuple(self.ids)])

    def _toggle_reconcile_to_false(self):
        '''Toggle the `reconcile´ boolean from True -> False

        Note that it is disallowed if some lines are partially reconciled.
        '''
        if not self.ids:
            return None
        partial_lines_count = self.env['account.move.line'].search_count([
            ('account_id', 'in', self.ids),
            ('full_reconcile_id', '=', False),
            ('|'),
            ('matched_debit_ids', '!=', False),
            ('matched_credit_ids', '!=', False),
        ])
        if partial_lines_count > 0:
            raise UserError(_('You cannot switch an account to prevent the reconciliation '
                              'if some partial reconciliations are still pending.'))
        query = """
            UPDATE account_move_line
                SET amount_residual = 0, amount_residual_currency = 0
            WHERE full_reconcile_id = NULL AND account_id IN %s
        """
        self.env.cr.execute(query, [tuple(self.ids)])

    def write(self, vals):
        # Do not allow changing the company_id when account_move_line already exist
        if vals.get('company_id', False):
            move_lines = self.env['account.move.line'].search([('account_id', 'in', self.ids)], limit=1)
            for account in self:
                if (account.company_id.id != vals['company_id']) and move_lines:
                    raise UserError(_('You cannot change the owner company of an account that already contains journal items.'))
        if 'reconcile' in vals:
            if vals['reconcile']:
                self.filtered(lambda r: not r.reconcile)._toggle_reconcile_to_true()
            else:
                self.filtered(lambda r: r.reconcile)._toggle_reconcile_to_false()

        if vals.get('currency_id'):
            for account in self:
                if self.env['account.move.line'].search_count([('account_id', '=', account.id), ('currency_id', 'not in', (False, vals['currency_id']))]):
                    raise UserError(_('You cannot set a currency on this account as it already has some journal entries having a different foreign currency.'))

        return super(AccountAccount, self).write(vals)

    def unlink(self):
        if self.env['account.move.line'].search([('account_id', 'in', self.ids)], limit=1):
            raise UserError(_('You cannot perform this action on an account that contains journal items.'))
        #Checking whether the account is set as a property to any Partner or not
        values = ['account.account,%s' % (account_id,) for account_id in self.ids]
        partner_prop_acc = self.env['ir.property'].search([('value_reference', 'in', values)], limit=1)
        if partner_prop_acc:
            raise UserError(_('You cannot remove/deactivate an account which is set on a customer or vendor.'))
        return super(AccountAccount, self).unlink()

    def action_open_reconcile(self):
        self.ensure_one()
        # Open reconciliation view for this account
        if self.internal_type == 'payable':
            action_context = {'show_mode_selector': False, 'mode': 'suppliers'}
        elif self.internal_type == 'receivable':
            action_context = {'show_mode_selector': False, 'mode': 'customers'}
        else:
            action_context = {'show_mode_selector': False, 'mode': 'accounts', 'account_ids': [self.id,]}
        return {
            'type': 'ir.actions.client',
            'tag': 'manual_reconciliation_view',
            'context': action_context,
        }


class AccountGroup(models.Model):
    _name = "account.group"
    _description = 'Account Group'
    _parent_store = True
    _order = 'code_prefix'

    parent_id = fields.Many2one('account.group', index=True, ondelete='cascade')
    parent_path = fields.Char(index=True)
    name = fields.Char(required=True)
    code_prefix = fields.Char()

    def name_get(self):
        result = []
        for group in self:
            name = group.name
            if group.code_prefix:
                name = group.code_prefix + ' ' + name
            result.append((group.id, name))
        return result

    @api.model
    def _name_search(self, name, args=None, operator='ilike', limit=100, name_get_uid=None):
        args = args or []
        if operator == 'ilike' and not (name or '').strip():
            domain = []
        else:
            criteria_operator = ['|'] if operator not in expression.NEGATIVE_TERM_OPERATORS else ['&', '!']
            domain = criteria_operator + [('code_prefix', '=ilike', name + '%'), ('name', operator, name)]
        group_ids = self._search(expression.AND([domain, args]), limit=limit, access_rights_uid=name_get_uid)
        return self.browse(group_ids).name_get()


class AccountJournalGroup(models.Model):
    _name = 'account.journal.group'
    _description = "Account Journal Group"

    name = fields.Char("Group Name", required=True, translate=True)
    company_id = fields.Many2one('res.company', required=True, default=lambda self: self.env.company)
    account_journal_ids = fields.Many2many('account.journal', string="Journals")
    sequence = fields.Integer(default=10)


class AccountJournal(models.Model):
    _name = "account.journal"
    _description = "Journal"
    _order = 'sequence, type, code'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    def _default_inbound_payment_methods(self):
        return self.env.ref('account.account_payment_method_manual_in')

    def _default_outbound_payment_methods(self):
        return self.env.ref('account.account_payment_method_manual_out')

    def __get_bank_statements_available_sources(self):
        return [('undefined', _('Undefined Yet'))]

    def _get_bank_statements_available_sources(self):
        return self.__get_bank_statements_available_sources()

    name = fields.Char(string='Journal Name', required=True)
    code = fields.Char(string='Short Code', size=5, required=True, help="The journal entries of this journal will be named using this prefix.")
    active = fields.Boolean(default=True, help="Set active to false to hide the Journal without removing it.")
    type = fields.Selection([
            ('sale', 'Sales'),
            ('purchase', 'Purchase'),
            ('cash', 'Cash'),
            ('bank', 'Bank'),
            ('general', 'Miscellaneous'),
        ], required=True,
        help="Select 'Sale' for customer invoices journals.\n"\
        "Select 'Purchase' for vendor bills journals.\n"\
        "Select 'Cash' or 'Bank' for journals that are used in customer or vendor payments.\n"\
        "Select 'General' for miscellaneous operations journals.")
    type_control_ids = fields.Many2many('account.account.type', 'account_journal_type_rel', 'journal_id', 'type_id', string='Account Types Allowed')
    account_control_ids = fields.Many2many('account.account', 'account_account_type_rel', 'journal_id', 'account_id', string='Accounts Allowed',
        domain=[('deprecated', '=', False)])
    default_credit_account_id = fields.Many2one('account.account', string='Default Credit Account',
        domain=[('deprecated', '=', False)], help="It acts as a default account for credit amount")
    default_debit_account_id = fields.Many2one('account.account', string='Default Debit Account',
        domain=[('deprecated', '=', False)], help="It acts as a default account for debit amount")
    update_posted = fields.Boolean(string='Allow Cancelling Entries',
        help="Check this box if you want to allow the cancellation the entries related to this journal or of the invoice related to this journal")
    sequence_id = fields.Many2one('ir.sequence', string='Entry Sequence',
        help="This field contains the information related to the numbering of the journal entries of this journal.", required=True, copy=False)
    refund_sequence_id = fields.Many2one('ir.sequence', string='Credit Note Entry Sequence',
        help="This field contains the information related to the numbering of the credit note entries of this journal.", copy=False)
    sequence = fields.Integer(help='Used to order Journals in the dashboard view', default=10)
    sequence_number_next = fields.Integer(string='Next Number',
        help='The next sequence number will be used for the next invoice.',
        compute='_compute_seq_number_next',
        inverse='_inverse_seq_number_next')
    refund_sequence_number_next = fields.Integer(string='Credit Notes: Next Number',
        help='The next sequence number will be used for the next credit note.',
        compute='_compute_refund_seq_number_next',
        inverse='_inverse_refund_seq_number_next')

    invoice_reference_type = fields.Selection(string='Communication Type', required=True, selection=[('none', 'Free'), ('partner', 'Based on Partner'), ('invoice', 'Based on Invoice')], default='invoice', help='You can set here the default communication that will appear on customer invoices, once validated, to help the customer to refer to that particular invoice when making the payment.')
    invoice_reference_model = fields.Selection(string='Communication Standard', required=True, selection=[('odoo', 'Odoo'),('euro', 'European')], default='odoo', help="You can choose different models for each type of reference. The default one is the Odoo reference.")

    #groups_id = fields.Many2many('res.groups', 'account_journal_group_rel', 'journal_id', 'group_id', string='Groups')
    currency_id = fields.Many2one('res.currency', help='The currency used to enter statement', string="Currency", oldname='currency')
    company_id = fields.Many2one('res.company', string='Company', required=True, index=True, default=lambda self: self.env.company,
        help="Company related to this journal")

    refund_sequence = fields.Boolean(string='Dedicated Credit Note Sequence', help="Check this box if you don't want to share the same sequence for invoices and credit notes made from this journal", default=False)

    inbound_payment_method_ids = fields.Many2many('account.payment.method', 'account_journal_inbound_payment_method_rel', 'journal_id', 'inbound_payment_method',
        domain=[('payment_type', '=', 'inbound')], string='For Incoming Payments', default=lambda self: self._default_inbound_payment_methods(),
        help="Manual: Get paid by cash, check or any other method outside of Odoo.\n"\
             "Electronic: Get paid automatically through a payment acquirer by requesting a transaction on a card saved by the customer when buying or subscribing online (payment token).\n"\
             "Batch Deposit: Encase several customer checks at once by generating a batch deposit to submit to your bank. When encoding the bank statement in Odoo,you are suggested to reconcile the transaction with the batch deposit. Enable this option from the settings.")
    outbound_payment_method_ids = fields.Many2many('account.payment.method', 'account_journal_outbound_payment_method_rel', 'journal_id', 'outbound_payment_method',
        domain=[('payment_type', '=', 'outbound')], string='For Outgoing Payments', default=lambda self: self._default_outbound_payment_methods(),
        help="Manual:Pay bill by cash or any other method outside of Odoo.\n"\
             "Check:Pay bill by check and print it from Odoo.\n"\
             "SEPA Credit Transfer: Pay bill from a SEPA Credit Transfer file you submit to your bank. Enable this option from the settings.")
    at_least_one_inbound = fields.Boolean(compute='_methods_compute', store=True)
    at_least_one_outbound = fields.Boolean(compute='_methods_compute', store=True)
    profit_account_id = fields.Many2one('account.account', string='Profit Account', domain=[('deprecated', '=', False)], help="Used to register a profit when the ending balance of a cash register differs from what the system computes")
    loss_account_id = fields.Many2one('account.account', string='Loss Account', domain=[('deprecated', '=', False)], help="Used to register a loss when the ending balance of a cash register differs from what the system computes")

    belongs_to_company = fields.Boolean('Belong to the user\'s current company', compute="_belong_to_company", search="_search_company_journals",)

    # Bank journals fields
    company_partner_id = fields.Many2one('res.partner', related='company_id.partner_id', string='Account Holder', readonly=True, store=False)
    bank_account_id = fields.Many2one('res.partner.bank', string="Bank Account", ondelete='restrict', copy=False, domain="[('partner_id','=', company_partner_id)]")
    bank_statements_source = fields.Selection(selection=_get_bank_statements_available_sources, string='Bank Feeds', default='undefined', help="Defines how the bank statements will be registered")
    bank_acc_number = fields.Char(related='bank_account_id.acc_number', readonly=False)
    bank_id = fields.Many2one('res.bank', related='bank_account_id.bank_id', readonly=False)
    post_at_bank_rec = fields.Boolean(string="Post At Bank Reconciliation", help="Whether or not the payments made in this journal should be generated in draft state, so that the related journal entries are only posted when performing bank reconciliation.")

    # alias configuration for journals
    alias_id = fields.Many2one('mail.alias', string='Alias', copy=False)
    alias_domain = fields.Char('Alias domain', compute='_compute_alias_domain', default=lambda self: self.env["ir.config_parameter"].sudo().get_param("mail.catchall.domain"))
    alias_name = fields.Char('Alias Name', related='alias_id.alias_name', help="It creates draft invoices and bills by sending an email.", readonly=False)

    journal_group_ids = fields.Many2many('account.journal.group', string="Journal Groups")

    _sql_constraints = [
        ('code_company_uniq', 'unique (code, name, company_id)', 'The code and name of the journal must be unique per company !'),
    ]

    def _compute_alias_domain(self):
        alias_domain = self.env["ir.config_parameter"].sudo().get_param("mail.catchall.domain")
        for record in self:
            record.alias_domain = alias_domain

    # do not depend on 'sequence_id.date_range_ids', because
    # sequence_id._get_current_sequence() may invalidate it!
    @api.depends('sequence_id.use_date_range', 'sequence_id.number_next_actual')
    def _compute_seq_number_next(self):
        '''Compute 'sequence_number_next' according to the current sequence in use,
        an ir.sequence or an ir.sequence.date_range.
        '''
        for journal in self:
            if journal.sequence_id:
                sequence = journal.sequence_id._get_current_sequence()
                journal.sequence_number_next = sequence.number_next_actual
            else:
                journal.sequence_number_next = 1

    def _inverse_seq_number_next(self):
        '''Inverse 'sequence_number_next' to edit the current sequence next number.
        '''
        for journal in self:
            if journal.sequence_id and journal.sequence_number_next:
                sequence = journal.sequence_id._get_current_sequence()
                sequence.sudo().number_next = journal.sequence_number_next

    # do not depend on 'refund_sequence_id.date_range_ids', because
    # refund_sequence_id._get_current_sequence() may invalidate it!
    @api.depends('refund_sequence_id.use_date_range', 'refund_sequence_id.number_next_actual')
    def _compute_refund_seq_number_next(self):
        '''Compute 'sequence_number_next' according to the current sequence in use,
        an ir.sequence or an ir.sequence.date_range.
        '''
        for journal in self:
            if journal.refund_sequence_id and journal.refund_sequence:
                sequence = journal.refund_sequence_id._get_current_sequence()
                journal.refund_sequence_number_next = sequence.number_next_actual
            else:
                journal.refund_sequence_number_next = 1

    def _inverse_refund_seq_number_next(self):
        '''Inverse 'refund_sequence_number_next' to edit the current sequence next number.
        '''
        for journal in self:
            if journal.refund_sequence_id and journal.refund_sequence and journal.refund_sequence_number_next:
                sequence = journal.refund_sequence_id._get_current_sequence()
                sequence.number_next = journal.refund_sequence_number_next

    @api.constrains('currency_id', 'default_credit_account_id', 'default_debit_account_id')
    def _check_currency(self):
        for journal in self:
            if journal.currency_id:
                if journal.default_credit_account_id and not journal.default_credit_account_id.currency_id.id == journal.currency_id.id:
                    raise ValidationError(_('The currency of the journal should be the same than the default credit account.'))
                if journal.default_debit_account_id and not journal.default_debit_account_id.currency_id.id == journal.currency_id.id:
                    raise ValidationError(_('The currency of the journal should be the same than the default debit account.'))

    @api.constrains('type', 'bank_account_id')
    def _check_bank_account(self):
        for journal in self:
            if journal.type == 'bank' and journal.bank_account_id:
                if journal.bank_account_id.company_id != journal.company_id:
                    raise ValidationError(_('The bank account of a bank journal must belong to the same company (%s).') % journal.company_id.name)
                # A bank account can belong to a customer/supplier, in which case their partner_id is the customer/supplier.
                # Or they are part of a bank journal and their partner_id must be the company's partner_id.
                if journal.bank_account_id.partner_id != journal.company_id.partner_id:
                    raise ValidationError(_('The holder of a journal\'s bank account must be the company (%s).') % journal.company_id.name)

    @api.onchange('default_debit_account_id')
    def onchange_debit_account_id(self):
        if not self.default_credit_account_id:
            self.default_credit_account_id = self.default_debit_account_id

    @api.onchange('default_credit_account_id')
    def onchange_credit_account_id(self):
        if not self.default_debit_account_id:
            self.default_debit_account_id = self.default_credit_account_id

    def _get_alias_values(self, type, alias_name=None):
        if not alias_name:
            alias_name = self.name
            if self.company_id != self.env.ref('base.main_company'):
                alias_name += '-' + str(self.company_id.name)
        return {
            'alias_defaults': {'type': type == 'purchase' and 'in_invoice' or 'out_invoice', 'company_id': self.company_id.id},
            'alias_parent_thread_id': self.id,
            'alias_name': re.sub(r'[^\w]+', '-', alias_name)
        }

    def unlink(self):
        bank_accounts = self.env['res.partner.bank'].browse()
        for bank_account in self.mapped('bank_account_id'):
            accounts = self.search([('bank_account_id', '=', bank_account.id)])
            if accounts <= self:
                bank_accounts += bank_account
        self.mapped('alias_id').unlink()
        ret = super(AccountJournal, self).unlink()
        bank_accounts.unlink()
        return ret

    @api.returns('self', lambda value: value.id)
    def copy(self, default=None):
        default = dict(default or {})
        default.update(
            code=_("%s (copy)") % (self.code or ''),
            name=_("%s (copy)") % (self.name or ''))
        return super(AccountJournal, self).copy(default)

    def _update_mail_alias(self, vals):
        self.ensure_one()
        alias_values = self._get_alias_values(type=vals.get('type') or self.type, alias_name=vals.get('alias_name'))
        if self.alias_id:
            self.alias_id.write(alias_values)
        else:
            self.alias_id = self.env['mail.alias'].with_context(alias_model_name='account.move',
                alias_parent_model_name='account.journal').create(alias_values)

        if vals.get('alias_name'):
            # remove alias_name to avoid useless write on alias
            del(vals['alias_name'])

    def write(self, vals):
        for journal in self:
            company = journal.company_id
            if ('company_id' in vals and journal.company_id.id != vals['company_id']):
                if self.env['account.move'].search([('journal_id', '=', journal.id)], limit=1):
                    raise UserError(_('This journal already contains items, therefore you cannot modify its company.'))
                company = self.env['res.company'].browse(vals['company_id'])
                if journal.bank_account_id.company_id and journal.bank_account_id.company_id != company:
                    journal.bank_account_id.write({
                        'company_id': company.id,
                        'partner_id': company.partner_id.id,
                    })
            if ('code' in vals and journal.code != vals['code']):
                if self.env['account.move'].search([('journal_id', 'in', self.ids)], limit=1):
                    raise UserError(_('This journal already contains items, therefore you cannot modify its short name.'))
                new_prefix = self._get_sequence_prefix(vals['code'], refund=False)
                journal.sequence_id.write({'prefix': new_prefix})
                if journal.refund_sequence_id:
                    new_prefix = self._get_sequence_prefix(vals['code'], refund=True)
                    journal.refund_sequence_id.write({'prefix': new_prefix})
            if 'currency_id' in vals:
                if not 'default_debit_account_id' in vals and journal.default_debit_account_id:
                    journal.default_debit_account_id.currency_id = vals['currency_id']
                if not 'default_credit_account_id' in vals and journal.default_credit_account_id:
                    journal.default_credit_account_id.currency_id = vals['currency_id']
                if journal.bank_account_id:
                    journal.bank_account_id.currency_id = vals['currency_id']
            if 'bank_account_id' in vals:
                if not vals.get('bank_account_id'):
                    raise UserError(_('You cannot remove the bank account from the journal once set.'))
                else:
                    bank_account = self.env['res.partner.bank'].browse(vals['bank_account_id'])
                    if bank_account.partner_id != company.partner_id:
                        raise UserError(_("The partners of the journal's company and the related bank account mismatch."))
            if 'alias_name' in vals:
                journal._update_mail_alias(vals)
        result = super(AccountJournal, self).write(vals)

        # Create the bank_account_id if necessary
        if 'bank_acc_number' in vals:
            for journal in self.filtered(lambda r: r.type == 'bank' and not r.bank_account_id):
                journal.set_bank_account(vals.get('bank_acc_number'), vals.get('bank_id'))
        # create the relevant refund sequence
        if vals.get('refund_sequence'):
            for journal in self.filtered(lambda j: j.type in ('sale', 'purchase') and not j.refund_sequence_id):
                journal_vals = {
                    'name': journal.name,
                    'company_id': journal.company_id.id,
                    'code': journal.code,
                    'refund_sequence_number_next': vals.get('refund_sequence_number_next', journal.refund_sequence_number_next),
                }
                journal.refund_sequence_id = self.sudo()._create_sequence(journal_vals, refund=True).id
        # Changing the 'post_at_bank_rec' option will post the draft payment moves and change the related invoices' state.
        if 'post_at_bank_rec' in vals and not vals['post_at_bank_rec']:
            draft_moves = self.env['account.move'].search([('journal_id', 'in', self.ids), ('state', '=', 'draft')])
            pending_payments = draft_moves.mapped('line_ids.payment_id')
            pending_payments.mapped('move_line_ids.move_id').post()
            pending_payments.mapped('reconciled_invoice_ids').filtered(lambda x: x.state == 'in_payment').write({'state': 'paid'})
        return result

    @api.model
    def _get_sequence_prefix(self, code, refund=False):
        prefix = code.upper()
        if refund:
            prefix = 'R' + prefix
        return prefix + '/%(range_year)s/'

    @api.model
    def _create_sequence(self, vals, refund=False):
        """ Create new no_gap entry sequence for every new Journal"""
        prefix = self._get_sequence_prefix(vals['code'], refund)
        seq_name = refund and vals['code'] + _(': Refund') or vals['code']
        seq = {
            'name': _('%s Sequence') % seq_name,
            'implementation': 'no_gap',
            'prefix': prefix,
            'padding': 4,
            'number_increment': 1,
            'use_date_range': True,
        }
        if 'company_id' in vals:
            seq['company_id'] = vals['company_id']
        seq = self.env['ir.sequence'].create(seq)
        seq_date_range = seq._get_current_sequence()
        seq_date_range.number_next = refund and vals.get('refund_sequence_number_next', 1) or vals.get('sequence_number_next', 1)
        return seq

    @api.model
    def _prepare_liquidity_account(self, name, company, currency_id, type):
        '''
        This function prepares the value to use for the creation of the default debit and credit accounts of a
        liquidity journal (created through the wizard of generating COA from templates for example).

        :param name: name of the bank account
        :param company: company for which the wizard is running
        :param currency_id: ID of the currency in which is the bank account
        :param type: either 'cash' or 'bank'
        :return: mapping of field names and values
        :rtype: dict
        '''
        digits = 6
        acc = self.env['account.account'].search([('company_id', '=', company.id)], limit=1)
        if acc:
            digits = len(acc.code)
        # Seek the next available number for the account code
        if type == 'bank':
            account_code_prefix = company.bank_account_code_prefix or ''
        else:
            account_code_prefix = company.cash_account_code_prefix or company.bank_account_code_prefix or ''

        liquidity_type = self.env.ref('account.data_account_type_liquidity')
        return {
                'name': name,
                'currency_id': currency_id or False,
                'code': self.env['account.account']._search_new_account_code(company, digits, account_code_prefix),
                'user_type_id': liquidity_type and liquidity_type.id or False,
                'company_id': company.id,
        }

    @api.model
    def get_next_bank_cash_default_code(self, journal_type, company_id):
        journal_code_base = (journal_type == 'cash' and 'CSH' or 'BNK')
        journals = self.env['account.journal'].search([('code', 'like', journal_code_base + '%'), ('company_id', '=', company_id)])
        for num in range(1, 100):
            # journal_code has a maximal size of 5, hence we can enforce the boundary num < 100
            journal_code = journal_code_base + str(num)
            if journal_code not in journals.mapped('code'):
                return journal_code

    @api.model
    def create(self, vals):
        company_id = vals.get('company_id', self.env.company.id)
        if vals.get('type') in ('bank', 'cash'):
            # For convenience, the name can be inferred from account number
            if not vals.get('name') and 'bank_acc_number' in vals:
                vals['name'] = vals['bank_acc_number']

            # If no code provided, loop to find next available journal code
            if not vals.get('code'):
                vals['code'] = self.get_next_bank_cash_default_code(vals['type'], company_id)
                if not vals['code']:
                    raise UserError(_("Cannot generate an unused journal code. Please fill the 'Shortcode' field."))

            # Create a default debit/credit account if not given
            default_account = vals.get('default_debit_account_id') or vals.get('default_credit_account_id')
            if not default_account:
                company = self.env['res.company'].browse(company_id)
                account_vals = self._prepare_liquidity_account(vals.get('name'), company, vals.get('currency_id'), vals.get('type'))
                default_account = self.env['account.account'].create(account_vals)
                vals['default_debit_account_id'] = default_account.id
                vals['default_credit_account_id'] = default_account.id

        # We just need to create the relevant sequences according to the chosen options
        if not vals.get('sequence_id'):
            vals.update({'sequence_id': self.sudo()._create_sequence(vals).id})
        if vals.get('type') in ('sale', 'purchase') and vals.get('refund_sequence') and not vals.get('refund_sequence_id'):
            vals.update({'refund_sequence_id': self.sudo()._create_sequence(vals, refund=True).id})
        journal = super(AccountJournal, self.with_context(mail_create_nolog=True)).create(vals)
        if 'alias_name' in vals:
            journal._update_mail_alias(vals)

        # Create the bank_account_id if necessary
        if journal.type == 'bank' and not journal.bank_account_id and vals.get('bank_acc_number'):
            journal.set_bank_account(vals.get('bank_acc_number'), vals.get('bank_id'))

        return journal

    def set_bank_account(self, acc_number, bank_id=None):
        """ Create a res.partner.bank and set it as value of the  field bank_account_id """
        self.ensure_one()
        self.bank_account_id = self.env['res.partner.bank'].create({
            'acc_number': acc_number,
            'bank_id': bank_id,
            'company_id': self.company_id.id,
            'currency_id': self.currency_id.id,
            'partner_id': self.company_id.partner_id.id,
        }).id

    def name_get(self):
        res = []
        for journal in self:
            currency = journal.currency_id or journal.company_id.currency_id
            name = "%s (%s)" % (journal.name, currency.name)
            res += [(journal.id, name)]
        return res

    @api.model
    def _name_search(self, name, args=None, operator='ilike', limit=100, name_get_uid=None):
        args = args or []

        if operator == 'ilike' and not (name or '').strip():
            domain = []
        else:
            connector = '&' if operator in expression.NEGATIVE_TERM_OPERATORS else '|'
            domain = [connector, ('code', operator, name), ('name', operator, name)]
        journal_ids = self._search(expression.AND([domain, args]), limit=limit, access_rights_uid=name_get_uid)
        return self.browse(journal_ids).name_get()

    @api.depends('company_id')
    def _belong_to_company(self):
        for journal in self:
            journal.belong_to_company = (journal.company_id.id == self.env.company.id)

    def _search_company_journals(self, operator, value):
        if value:
            recs = self.search([('company_id', operator, self.env.company.id)])
        elif operator == '=':
            recs = self.search([('company_id', '!=', self.env.company.id)])
        else:
            recs = self.search([('company_id', operator, self.env.company.id)])
        return [('id', 'in', [x.id for x in recs])]

    @api.depends('inbound_payment_method_ids', 'outbound_payment_method_ids')
    def _methods_compute(self):
        for journal in self:
            journal.at_least_one_inbound = bool(len(journal.inbound_payment_method_ids))
            journal.at_least_one_outbound = bool(len(journal.outbound_payment_method_ids))

    def action_configure_bank_journal(self):
        """ This function is called by the "configure" button of bank journals,
        visible on dashboard if no bank statement source has been defined yet
        """
        # We simply call the setup bar function.
        return self.env['res.company'].setting_init_bank_account_action()


class ResPartnerBank(models.Model):
    _inherit = "res.partner.bank"

    journal_id = fields.One2many('account.journal', 'bank_account_id', domain=[('type', '=', 'bank')], string='Account Journal', readonly=True,
        help="The accounting journal corresponding to this bank account.")

    @api.constrains('journal_id')
    def _check_journal_id(self):
        for bank in self:
            if len(bank.journal_id) > 1:
                raise ValidationError(_('A bank account can belong to only one journal.'))


#----------------------------------------------------------
# Tax
#----------------------------------------------------------

class AccountTaxGroup(models.Model):
    _name = 'account.tax.group'
    _description = 'Tax Group'
    _order = 'sequence asc'

    name = fields.Char(required=True, translate=True)
    sequence = fields.Integer(default=10)
    property_tax_payable_account_id = fields.Many2one('account.account', company_dependent=True, string='Tax current account (payable)')
    property_tax_receivable_account_id = fields.Many2one('account.account', company_dependent=True, string='Tax current account (receivable)')
    property_advance_tax_payment_account_id = fields.Many2one('account.account', company_dependent=True, string='Advance Tax payment account')


class AccountTax(models.Model):
    _name = 'account.tax'
    _description = 'Tax'
    _order = 'sequence,id'

    @api.model
    def _default_tax_group(self):
        return self.env['account.tax.group'].search([], limit=1)

    name = fields.Char(string='Tax Name', required=True)
    type_tax_use = fields.Selection([('sale', 'Sales'), ('purchase', 'Purchases'), ('none', 'None'), ('adjustment', 'Adjustment')], string='Tax Scope', required=True, default="sale",
        help="Determines where the tax is selectable. Note : 'None' means a tax can't be used by itself, however it can still be used in a group. 'adjustment' is used to perform tax adjustment.")
    amount_type = fields.Selection(default='percent', string="Tax Computation", required=True, oldname='type',
        selection=[('group', 'Group of Taxes'), ('fixed', 'Fixed'), ('percent', 'Percentage of Price'), ('division', 'Percentage of Price Tax Included')],
        help="""
    - Group of Taxes: The tax is a set of sub taxes.
    - Fixed: The tax amount stays the same whatever the price.
    - Percentage of Price: The tax amount is a % of the price:
        e.g 100 * 10% = 110 (not price included)
        e.g 110 / (1 + 10%) = 100 (price included)
    - Percentage of Price Tax Included: The tax amount is a division of the price:
        e.g 180 / (1 - 10%) = 200 (not price included)
        e.g 200 * (1 - 10%) = 180 (price included)
        """)
    active = fields.Boolean(default=True, help="Set active to false to hide the tax without removing it.")
    company_id = fields.Many2one('res.company', string='Company', required=True, default=lambda self: self.env.company)
    children_tax_ids = fields.Many2many('account.tax', 'account_tax_filiation_rel', 'parent_tax', 'child_tax', string='Children Taxes')
    sequence = fields.Integer(required=True, default=1,
        help="The sequence field is used to define order in which the tax lines are applied.")
    amount = fields.Float(required=True, digits=(16, 4))
    description = fields.Char(string='Label on Invoices')
    price_include = fields.Boolean(string='Included in Price', default=False,
        help="Check this if the price you use on the product and invoices includes this tax.")
    include_base_amount = fields.Boolean(string='Affect Base of Subsequent Taxes', default=False,
        help="If set, taxes which are computed after this one will be computed based on the price tax included.")
    analytic = fields.Boolean(string="Include in Analytic Cost", help="If set, the amount computed by this tax will be assigned to the same analytic account as the invoice line (if any)")
    tax_group_id = fields.Many2one('account.tax.group', string="Tax Group", default=_default_tax_group, required=True)
    # Technical field to make the 'tax_exigibility' field invisible if the same named field is set to false in 'res.company' model
    hide_tax_exigibility = fields.Boolean(string='Hide Use Cash Basis Option', related='company_id.tax_exigibility', readonly=True)
    tax_exigibility = fields.Selection(
        [('on_invoice', 'Based on Invoice'),
         ('on_payment', 'Based on Payment'),
        ], string='Tax Due', default='on_invoice',
        oldname='use_cash_basis',
        help="Based on Invoice: the tax is due as soon as the invoice is validated.\n"
        "Based on Payment: the tax is due as soon as the payment of the invoice is received.")
    cash_basis_transition_account_id = fields.Many2one(string="Cash Basis Transition Account", domain=[('deprecated', '=', False)], comodel_name='account.account', help="Account used to transition the tax amount for cash basis taxes. It will contain the tax amount as long as the original invoice has not been reconciled ; at reconciliation, this amount cancelled on this account and put on the regular tax account.")
    cash_basis_base_account_id = fields.Many2one(
        'account.account',
        domain=[('deprecated', '=', False)],
        string='Base Tax Received Account',
        help='Account that will be set on lines created in cash basis journal entry and used to keep track of the tax base amount.')
    invoice_repartition_line_ids = fields.One2many(string="Repartition for Invoices", comodel_name="account.tax.repartition.line", inverse_name="invoice_tax_id", copy=True, help="Repartition when the tax is used on an invoice")
    refund_repartition_line_ids = fields.One2many(string="Repartition for Refund Invoices", comodel_name="account.tax.repartition.line", inverse_name="refund_tax_id", copy=True, help="Repartition when the tax is used on a refund")
    country_id = fields.Many2one(string='Country', comodel_name='res.country', related='company_id.country_id', help="Technical field used to restrict the domain of account tags for tax repartition lines created for this tax.")

    _sql_constraints = [
        ('name_company_uniq', 'unique(name, company_id, type_tax_use)', 'Tax names must be unique !'),
    ]

    @api.model
    def default_get(self, vals):
        # company_id is added so that we are sure to fetch a default value from it to use in repartition lines, below
        rslt = super(AccountTax, self).default_get(vals + ['company_id'])

        company_id = rslt.get('company_id')
        company = self.env['res.company'].browse(company_id)

        if 'refund_repartition_line_ids' in vals:
            # We write on the related country_id field so that the field is recomputed. Without that, it will stay empty until we save the record.
            rslt['refund_repartition_line_ids'] = [
                (0, 0, { 'repartition_type': 'base', 'factor_percent': 100.0, 'tag_ids': [], 'company_id': company_id, 'country_id': company.country_id.id}),
                (0, 0, { 'repartition_type': 'tax', 'factor_percent': 100.0, 'tag_ids': [], 'company_id': company_id, 'country_id': company.country_id.id}),
            ]

        if 'invoice_repartition_line_ids' in vals:
            # We write on the related country_id field so that the field is recomputed. Without that, it will stay empty until we save the record.
            rslt['invoice_repartition_line_ids'] = [
                (0, 0, { 'repartition_type': 'base', 'factor_percent': 100.0, 'tag_ids': [], 'company_id': company_id, 'country_id': company.country_id.id}),
                (0, 0, { 'repartition_type': 'tax', 'factor_percent': 100.0, 'tag_ids': [], 'company_id': company_id, 'country_id': company.country_id.id}),
            ]

        return rslt

    def _check_repartition_lines(self, lines):
        self.ensure_one()

        base_line = lines.filtered(lambda x: x.repartition_type == 'base')
        if len(base_line) != 1:
            raise ValidationError(_("Invoice and credit note repartition should each contain exactly one line for the base."))

    @api.constrains('invoice_repartition_line_ids', 'refund_repartition_line_ids')
    def _validate_repartition_lines(self):
        for record in self:
            record._check_repartition_lines(record.invoice_repartition_line_ids)
            record._check_repartition_lines(record.refund_repartition_line_ids)

            if len(record.invoice_repartition_line_ids) != len(record.refund_repartition_line_ids):
                raise ValidationError(_("Invoice and credit note repartition should have the same number of lines."))

            index = 0
            while index < len(record.invoice_repartition_line_ids):
                inv_rep_ln = record.invoice_repartition_line_ids[index]
                ref_rep_ln = record.refund_repartition_line_ids[index]
                if inv_rep_ln.repartition_type != ref_rep_ln.repartition_type or inv_rep_ln.factor_percent != ref_rep_ln.factor_percent:
                    raise ValidationError(_("Invoice and credit note repartitions should match (same percentages, in the same order)."))
                index += 1

    @api.constrains('children_tax_ids', 'type_tax_use')
    def _check_children_scope(self):
        for tax in self:
            if not tax._check_m2m_recursion('children_tax_ids'):
                raise ValidationError(_("Recursion found for tax '%s'.") % (tax.name,))
            if not all(child.type_tax_use in ('none', tax.type_tax_use) for child in tax.children_tax_ids):
                raise ValidationError(_('The application scope of taxes in a group must be either the same as the group or left empty.'))

    @api.returns('self', lambda value: value.id)
    def copy(self, default=None):
        default = dict(default or {}, name=_("%s (Copy)") % self.name)
        return super(AccountTax, self).copy(default=default)

    def name_get(self):
        if not self._context.get('append_type_to_tax_name'):
            return super(AccountTax, self).name_get()
        tax_type = dict(self._fields['type_tax_use']._description_selection(self.env))
        return [(tax.id, '%s (%s)' % (tax.name, tax_type.get(tax.type_tax_use))) for tax in self]

    @api.model
    def _name_search(self, name, args=None, operator='ilike', limit=100, name_get_uid=None):
        """ Returns a list of tuples containing id, name, as internally it is called {def name_get}
            result format: {[(id, name), (id, name), ...]}
        """
        args = args or []
        if operator == 'ilike' and not (name or '').strip():
            domain = []
        else:
            connector = '&' if operator in expression.NEGATIVE_TERM_OPERATORS else '|'
            domain = [connector, ('description', operator, name), ('name', operator, name)]
        tax_ids = self._search(expression.AND([domain, args]), limit=limit, access_rights_uid=name_get_uid)
        return self.browse(tax_ids).name_get()

    @api.model
    def _search(self, args, offset=0, limit=None, order=None, count=False, access_rights_uid=None):
        context = self._context or {}

        if context.get('type'):
            if context.get('type') in ('out_invoice', 'out_refund'):
                args += [('type_tax_use', '=', 'sale')]
            elif context.get('type') in ('in_invoice', 'in_refund'):
                args += [('type_tax_use', '=', 'purchase')]

        if context.get('journal_id'):
            journal = self.env['account.journal'].browse(context.get('journal_id'))
            if journal.type in ('sale', 'purchase'):
                args += [('type_tax_use', '=', journal.type)]

        return super(AccountTax, self)._search(args, offset, limit, order, count=count, access_rights_uid=access_rights_uid)

    @api.onchange('amount')
    def onchange_amount(self):
        if self.amount_type in ('percent', 'division') and self.amount != 0.0 and not self.description:
            self.description = "{0:.4g}%".format(self.amount)

    @api.onchange('amount_type')
    def onchange_amount_type(self):
        if self.amount_type is not 'group':
            self.children_tax_ids = [(5,)]

    @api.onchange('price_include')
    def onchange_price_include(self):
        if self.price_include:
            self.include_base_amount = True

    def _compute_amount(self, base_amount, price_unit, quantity=1.0, product=None, partner=None):
        """ Returns the amount of a single tax. base_amount is the actual amount on which the tax is applied, which is
            price_unit * quantity eventually affected by previous taxes (if tax is include_base_amount XOR price_include)
        """
        self.ensure_one()

        if self.amount_type == 'fixed':
            # Use copysign to take into account the sign of the base amount which includes the sign
            # of the quantity and the sign of the price_unit
            # Amount is the fixed price for the tax, it can be negative
            # Base amount included the sign of the quantity and the sign of the unit price and when
            # a product is returned, it can be done either by changing the sign of quantity or by changing the
            # sign of the price unit.
            # When the price unit is equal to 0, the sign of the quantity is absorbed in base_amount then
            # a "else" case is needed.
            if base_amount:
                return math.copysign(quantity, base_amount) * self.amount
            else:
                return quantity * self.amount

        price_include = self._context.get('force_price_include', self.price_include)

        # base * (1 + tax_amount) = new_base
        if self.amount_type == 'percent' and not price_include:
            return base_amount * self.amount / 100
        # <=> new_base = base / (1 + tax_amount)
        if self.amount_type == 'percent' and price_include:
            return base_amount - (base_amount / (1 + self.amount / 100))
        # base / (1 - tax_amount) = new_base
        if self.amount_type == 'division' and not price_include:
            return base_amount / (1 - self.amount / 100) - base_amount
        # <=> new_base * (1 - tax_amount) = base
        if self.amount_type == 'division' and price_include:
            return base_amount - (base_amount * (self.amount / 100))

    def json_friendly_compute_all(self, price_unit, currency_id=None, quantity=1.0, product_id=None, partner_id=None, is_refund=False):
        """ Just converts parameters in browse records and calls for compute_all, because js widgets can't serialize browse records """
        if currency_id:
            currency_id = self.env['res.currency'].browse(currency_id)
        if product_id:
            product_id = self.env['product.product'].browse(product_id)
        if partner_id:
            partner_id = self.env['res.partner'].browse(partner_id)
        return self.compute_all(price_unit, currency=currency_id, quantity=quantity, product=product_id, partner=partner_id, is_refund=is_refund)

    def flatten_taxes_hierarchy(self):
        # Flattens the taxes contained in this recordset, returning all the
        # children at the bottom of the hierarchy, in a recordset, ordered by sequence.
        #   Eg. considering letters as taxes and alphabetic order as sequence :
        #   [G, B([A, D, F]), E, C] will be computed as [A, D, F, C, E, G]
        all_taxes = self.env['account.tax']
        for tax in self.sorted(key=lambda r: r.sequence):
            if tax.amount_type == 'group':
                all_taxes += tax.children_tax_ids.flatten_taxes_hierarchy()
            else:
                all_taxes += tax
        return all_taxes

    def get_tax_tags(self, is_refund, repartition_type):
        rep_lines = self.mapped(is_refund and 'refund_repartition_line_ids' or 'invoice_repartition_line_ids')
        return rep_lines.filtered(lambda x: x.repartition_type == repartition_type).mapped('tag_ids')

    def compute_all(self, price_unit, currency=None, quantity=1.0, product=None, partner=None, is_refund=False, handle_price_include=True):
        """ Returns all information required to apply taxes (in self + their children in case of a tax group).
            We consider the sequence of the parent for group of taxes.
                Eg. considering letters as taxes and alphabetic order as sequence :
                [G, B([A, D, F]), E, C] will be computed as [A, D, F, C, E, G]

            'handle_price_include' is used when we need to ignore all tax included in price. If False, it means the
            amount passed to this method will be considered as the base of all computations.

        RETURN: {
            'total_excluded': 0.0,    # Total without taxes
            'total_included': 0.0,    # Total with taxes
            'total_void'    : 0.0,    # Total with those taxes, that don't have an account set
            'taxes': [{               # One dict for each tax in self and their children
                'id': int,
                'name': str,
                'amount': float,
                'sequence': int,
                'account_id': int,
                'refund_account_id': int,
                'analytic': boolean,
            }],
        } """
        if not self:
            company = self.env.company
        else:
            company = self[0].company_id

        # 1) Flatten the taxes.
        taxes = self.flatten_taxes_hierarchy()

        # 2) Avoid mixing taxes having price_include=False && include_base_amount=True
        # with taxes having price_include=True. This use case is not supported as the
        # computation of the total_excluded would be impossible.
        base_excluded_flag = False  # price_include=False && include_base_amount=True
        included_flag = False  # price_include=True
        for tax in taxes:
            if tax.price_include:
                included_flag = True
            elif tax.include_base_amount:
                base_excluded_flag = True
            if base_excluded_flag and included_flag:
                raise UserError(_('Unable to mix any taxes being price included with taxes affecting the base amount but not included in price.'))

        # 3) Deal with the rounding methods
        if not currency:
            currency = company.currency_id
        # By default, for each tax, tax amount will first be computed
        # and rounded at the 'Account' decimal precision for each
        # PO/SO/invoice line and then these rounded amounts will be
        # summed, leading to the total amount for that tax. But, if the
        # company has tax_calculation_rounding_method = round_globally,
        # we still follow the same method, but we use a much larger
        # precision when we round the tax amount for each line (we use
        # the 'Account' decimal precision + 5), and that way it's like
        # rounding after the sum of the tax amounts of each line
        prec = currency.decimal_places

        # In some cases, it is necessary to force/prevent the rounding of the tax and the total
        # amounts. For example, in SO/PO line, we don't want to round the price unit at the
        # precision of the currency.
        # The context key 'round' allows to force the standard behavior.
        round_tax = False if company.tax_calculation_rounding_method == 'round_globally' else True
        round_total = True
        if 'round' in self.env.context:
            round_tax = bool(self.env.context['round'])
            round_total = bool(self.env.context['round'])

        if not round_tax:
            prec += 5

        # 4) Iterate the taxes in the reversed sequence order to retrieve the initial base of the computation.
        #     tax  |  base  |  amount  |
        # /\ ----------------------------
        # || tax_1 |  XXXX  |          | <- we are looking for that, it's the total_excluded
        # || tax_2 |   ..   |          |
        # || tax_3 |   ..   |          |
        # ||  ...  |   ..   |    ..    |
        #    ----------------------------
        def recompute_base(base_amount, fixed_amount, percent_amount, division_amount):
            # Recompute the new base amount based on included fixed/percent amounts and the current base amount.
            # Example:
            #  tax  |  amount  |   type   |  price_include  |
            # -----------------------------------------------
            # tax_1 |   10%    | percent  |  t
            # tax_2 |   15     |   fix    |  t
            # tax_3 |   20%    | percent  |  t
            # tax_4 |   10%    | division |  t
            # -----------------------------------------------

            # if base_amount = 145, the new base is computed as:
            # (145 - 15) / (1.0 + 30%) * 90% = 130 / 1.3 * 90% = 90
            return (base_amount - fixed_amount) / (1.0 + percent_amount / 100.0) * (100 - division_amount) / 100

        base = round(price_unit * quantity, prec)

        # For the computation of move lines, we could have a negative base value.
        # In this case, compute all with positive values and negate them at the end.
        sign = 1
        if base < 0:
            base = -base
            sign = -1

        # Store the totals to reach when using price_include taxes (only the last price included in row)
        total_included_checkpoints = {}
        i = len(taxes) - 1
        store_included_tax_total = True
        # Keep track of the accumulated included fixed/percent amount.
        incl_fixed_amount = incl_percent_amount = incl_division_amount = 0
        # Store the tax amounts we compute while searching for the total_excluded
        cached_tax_amounts = {}
        if handle_price_include:
            for tax in reversed(taxes):
                if tax.include_base_amount:
                    base = recompute_base(base, incl_fixed_amount, incl_percent_amount, incl_division_amount)
                    incl_fixed_amount = incl_percent_amount = incl_division_amount = 0
                    store_included_tax_total = True
                if tax.price_include or self._context.get('force_price_include'):
                    if tax.amount_type == 'percent':
                        incl_percent_amount += tax.amount
                    elif tax.amount_type == 'division':
                        incl_division_amount += tax.amount
                    elif tax.amount_type == 'fixed':
                        incl_fixed_amount += quantity * tax.amount
                    else:
                        # tax.amount_type == other (python)
                        tax_amount = tax._compute_amount(base, price_unit, quantity, product, partner)
                        incl_fixed_amount += tax_amount
                        # Avoid unecessary re-computation
                        cached_tax_amounts[i] = tax_amount
                    if store_included_tax_total:
                        total_included_checkpoints[i] = base
                        store_included_tax_total = False
                i -= 1

        total_excluded = recompute_base(base, incl_fixed_amount, incl_percent_amount, incl_division_amount)

        # 5) Iterate the taxes in the sequence order to compute missing tax amounts.
        # Start the computation of accumulated amounts at the total_excluded value.
        base = total_included = total_void = total_excluded

        taxes_vals = []
        i = 0
        cumulated_tax_included_amount = 0
        for tax in taxes:
            #compute the tax_amount
            if (self._context.get('force_price_include') or tax.price_include) and total_included_checkpoints.get(i):
                # We know the total to reach for that tax, so we make a substraction to avoid any rounding issues
                tax_amount = total_included_checkpoints[i] - (base + cumulated_tax_included_amount)
                cumulated_tax_included_amount = 0
            else:
                tax_amount = tax.with_context(force_price_include=False)._compute_amount(
                    base, price_unit, quantity, product, partner)

            # Round the tax_amount
            tax_amount = round(tax_amount, prec)

            if tax.price_include and not total_included_checkpoints.get(i):
                cumulated_tax_included_amount += tax_amount

            # If the tax affects the base of subsequent taxes, its tax move lines must
            # receive the base tags and tag_ids of these taxes, so that the tax report computes
            # the right total
            subsequent_taxes = self.env['account.tax']
            subsequent_tags = self.env['account.account.tag']
            if tax.include_base_amount:
                subsequent_taxes = taxes[i+1:]
                subsequent_tags = subsequent_taxes.get_tax_tags(is_refund, 'base')

            # Compute the tax lines
            tax_repartition_lines = is_refund and tax.refund_repartition_line_ids or tax.invoice_repartition_line_ids
            repartition_lines_to_treat = len(tax_repartition_lines)
            total_amount = 0
            for repartition_line in tax_repartition_lines.filtered(lambda x: x.repartition_type == 'tax'):
                # In case some rounding error occurs, we compensate for it on the last line
                line_amount = round(sign * tax_amount * repartition_line.factor if repartition_lines_to_treat != 1 else tax_amount - total_amount, prec)

                taxes_vals.append({
                    'id': tax.id,
                    'name': partner and tax.with_context(lang=partner.lang).name or tax.name,
                    'amount': line_amount,
                    'base': round(sign * base, prec),
                    'sequence': tax.sequence,
                    'account_id': tax.cash_basis_transition_account_id.id if tax.tax_exigibility == 'on_payment' else repartition_line.account_id.id,
                    'analytic': tax.analytic,
                    'price_include': tax.price_include or self._context.get('force_price_include'),
                    'tax_exigibility': tax.tax_exigibility,
                    'tax_repartition_line_id': repartition_line.id,
                    'tag_ids': (repartition_line.tag_ids + subsequent_tags).ids,
                    'tax_ids': subsequent_taxes.ids,
                })

                total_amount += line_amount
                if not repartition_line.account_id:
                    total_void += line_amount
                repartition_lines_to_treat -= 1

            # Affect subsequent taxes
            if tax.include_base_amount:
                base += tax_amount

            total_included += tax_amount
            i += 1

        return {
            'base_tags': taxes.mapped(is_refund and 'refund_repartition_line_ids' or 'invoice_repartition_line_ids').filtered(lambda x: x.repartition_type == 'base').mapped('tag_ids').ids,
            'taxes': taxes_vals,
            'total_excluded': sign * (currency.round(total_excluded) if round_total else total_excluded),
            'total_included': sign * (currency.round(total_included) if round_total else total_included),
            'total_void': sign * (currency.round(total_void) if round_total else total_void),
        }

    @api.model
    def _fix_tax_included_price(self, price, prod_taxes, line_taxes):
        """Subtract tax amount from price when corresponding "price included" taxes do not apply"""
        # FIXME get currency in param?
        prod_taxes = prod_taxes._origin
        line_taxes = line_taxes._origin
        incl_tax = prod_taxes.filtered(lambda tax: tax not in line_taxes and tax.price_include)
        if incl_tax:
            return incl_tax.compute_all(price)['total_excluded']
        return price

    @api.model
    def _fix_tax_included_price_company(self, price, prod_taxes, line_taxes, company_id):
        if company_id:
            #To keep the same behavior as in _compute_tax_id
            prod_taxes = prod_taxes.filtered(lambda tax: tax.company_id == company_id)
            line_taxes = line_taxes.filtered(lambda tax: tax.company_id == company_id)
        return self._fix_tax_included_price(price, prod_taxes, line_taxes)


class AccountTaxRepartitionLine(models.Model):
    _name = "account.tax.repartition.line"
    _description = "Tax Repartition Line"
    _order = 'sequence, repartition_type, id'

    factor_percent = fields.Float(string="%", required=True, help="Factor to apply on the account move lines generated from this repartition line, in percents")
    factor = fields.Float(string="Factor Ratio", compute="_compute_factor", help="Factor to apply on the account move lines generated from this repartition line")
    repartition_type = fields.Selection(string="Based On", selection=[('base', 'Base'), ('tax', 'of tax')], required=True, default='tax', help="Base on which the factor will be applied.")
    account_id = fields.Many2one(string="Account", comodel_name='account.account', help="Account on which to post the tax amount")
    tag_ids = fields.Many2many(string="Tax Grid", comodel_name='account.account.tag', domain=[('applicability', '=', 'taxes')], copy=True)
    invoice_tax_id = fields.Many2one(comodel_name='account.tax', help="The tax set to apply this repartition on invoices. Mutually exclusive with refund_tax_id")
    refund_tax_id = fields.Many2one(comodel_name='account.tax', help="The tax set to apply this repartition on refund invoices. Mutually exclusive with invoice_tax_id")
    tax_id = fields.Many2one(comodel_name='account.tax', compute='_compute_tax_id')
    country_id = fields.Many2one(string="Country", comodel_name='res.country', related='company_id.country_id',  help="Technical field used to restrict tags domain in form view.")
    company_id = fields.Many2one(string="Company", comodel_name='res.company', required=True, default=lambda x: x.env.user.company_id, help="The company this repartition line belongs to.")
    sequence = fields.Integer(string="Sequence", default=1, help="The order in which display and match repartition lines. For refunds to work properly, invoice repartition lines should be arranged in the same order as the credit note repartition lines they correspond to.")

    @api.constrains('invoice_tax_id', 'refund_tax_id')
    def validate_tax_template_link(self):
        for record in self:
            if record.invoice_tax_id and record.refund_tax_id:
                raise ValidationError(_("Tax repartition lines should apply to either invoices or refunds, not both at the same time. invoice_tax_id and refund_tax_id should not be set together."))

    @api.depends('factor_percent')
    def _compute_factor(self):
        for record in self:
            record.factor = record.factor_percent / 100.0

    @api.depends('invoice_tax_id', 'refund_tax_id')
    def _compute_tax_id(self):
        for record in self:
            record.tax_id = record.invoice_tax_id or record.refund_tax_id

    @api.onchange('repartition_type')
    def _onchange_repartition_type(self):
        if self.repartition_type == 'base':
            self.account_id = None
