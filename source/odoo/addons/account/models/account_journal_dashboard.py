import json
from datetime import datetime, timedelta

from babel.dates import format_datetime, format_date
from odoo import models, api, _, fields
from odoo.osv import expression
from odoo.release import version
from odoo.tools import DEFAULT_SERVER_DATE_FORMAT as DF, safe_eval
from odoo.tools.misc import formatLang, format_date as odoo_format_date
import random

import ast


class account_journal(models.Model):
    _inherit = "account.journal"

    def _kanban_dashboard(self):
        for journal in self:
            journal.kanban_dashboard = json.dumps(journal.get_journal_dashboard_datas())

    def _kanban_dashboard_graph(self):
        for journal in self:
            if (journal.type in ['sale', 'purchase']):
                journal.kanban_dashboard_graph = json.dumps(journal.get_bar_graph_datas())
            elif (journal.type in ['cash', 'bank']):
                journal.kanban_dashboard_graph = json.dumps(journal.get_line_graph_datas())

    def _get_json_activity_data(self):
        for journal in self:
            activities = []
            # search activity on move on the journal
            sql_query = '''
                SELECT act.id,
                    act.res_id,
                    act.res_model,
                    act.summary,
                    act_type.name as act_type_name,
                    act_type.category as activity_category,
                    act.date_deadline,
                    CASE WHEN act.date_deadline < CURRENT_DATE THEN 'late' ELSE 'future' END as status
                FROM account_move m
                    LEFT JOIN mail_activity act ON act.res_id = m.id
                    LEFT JOIN mail_activity_type act_type ON act.activity_type_id = act_type.id
                WHERE act.res_model = 'account.move'
                    AND m.journal_id = %s
            '''
            self.env.cr.execute(sql_query, (journal.id,))
            for activity in self.env.cr.dictfetchall():
                activities.append({
                    'id': activity.get('id'),
                    'res_id': activity.get('res_id'),
                    'res_model': activity.get('res_model'),
                    'status': activity.get('status'),
                    'name': activity.get('summary') or activity.get('act_type_name'),
                    'activity_category': activity.get('activity_category'),
                    'date': odoo_format_date(self.env, activity.get('date_deadline'))
                })
            journal.json_activity_data = json.dumps({'activities': activities})

    kanban_dashboard = fields.Text(compute='_kanban_dashboard')
    kanban_dashboard_graph = fields.Text(compute='_kanban_dashboard_graph')
    json_activity_data = fields.Text(compute='_get_json_activity_data')
    show_on_dashboard = fields.Boolean(string='Show journal on dashboard', help="Whether this journal should be displayed on the dashboard or not", default=True)
    color = fields.Integer("Color Index", default=0)

    def _graph_title_and_key(self):
        if self.type in ['sale', 'purchase']:
            return ['', _('Residual amount')]
        elif self.type == 'cash':
            return ['', _('Cash: Balance')]
        elif self.type == 'bank':
            return ['', _('Bank: Balance')]

    # Below method is used to get data of bank and cash statemens
    def get_line_graph_datas(self):
        """Computes the data used to display the graph for bank and cash journals in the accounting dashboard"""

        def build_graph_data(date, amount):
            #display date in locale format
            name = format_date(date, 'd LLLL Y', locale=locale)
            short_name = format_date(date, 'd MMM', locale=locale)
            return {'x':short_name,'y': amount, 'name':name}

        self.ensure_one()
        BankStatement = self.env['account.bank.statement']
        data = []
        today = datetime.today()
        last_month = today + timedelta(days=-30)
        locale = self._context.get('lang') or 'en_US'

        #starting point of the graph is the last statement
        last_stmt = BankStatement.search([('journal_id', '=', self.id), ('date', '<=', today.strftime(DF))], order='date desc, id desc', limit=1)

        last_balance = last_stmt and last_stmt.balance_end_real or 0
        data.append(build_graph_data(today, last_balance))

        #then we subtract the total amount of bank statement lines per day to get the previous points
        #(graph is drawn backward)
        date = today
        amount = last_balance
        query = """SELECT l.date, sum(l.amount) as amount
                        FROM account_bank_statement_line l
                        RIGHT JOIN account_bank_statement st ON l.statement_id = st.id
                        WHERE st.journal_id = %s
                          AND l.date > %s
                          AND l.date <= %s
                        GROUP BY l.date
                        ORDER BY l.date desc
                        """
        self.env.cr.execute(query, (self.id, last_month, today))
        query_result = self.env.cr.dictfetchall()
        for val in query_result:
            date = val['date']
            if date != today.strftime(DF):  # make sure the last point in the graph is today
                data[:0] = [build_graph_data(date, amount)]
            amount -= val['amount']

        # make sure the graph starts 1 month ago
        if date.strftime(DF) != last_month.strftime(DF):
            data[:0] = [build_graph_data(last_month, amount)]

        [graph_title, graph_key] = self._graph_title_and_key()
        color = '#875A7B' if 'e' in version else '#7c7bad'

        is_sample_data = not last_stmt and len(query_result) == 0
        if is_sample_data:
            data = []
            for i in range(30, 0, -5):
                current_date = today + timedelta(days=-i)
                data.append(build_graph_data(current_date, random.randint(-5, 15)))

        return [{'values': data, 'title': graph_title, 'key': graph_key, 'area': True, 'color': color, 'is_sample_data': is_sample_data}]

    def get_bar_graph_datas(self):
        data = []
        today = fields.Datetime.now(self)
        data.append({'label': _('Past'), 'value':0.0, 'type': 'past'})
        day_of_week = int(format_datetime(today, 'e', locale=self._context.get('lang') or 'en_US'))
        first_day_of_week = today + timedelta(days=-day_of_week+1)
        for i in range(-1,4):
            if i==0:
                label = _('This Week')
            elif i==3:
                label = _('Future')
            else:
                start_week = first_day_of_week + timedelta(days=i*7)
                end_week = start_week + timedelta(days=6)
                if start_week.month == end_week.month:
                    label = str(start_week.day) + '-' +str(end_week.day)+ ' ' + format_date(end_week, 'MMM', locale=self._context.get('lang') or 'en_US')
                else:
                    label = format_date(start_week, 'd MMM', locale=self._context.get('lang') or 'en_US')+'-'+format_date(end_week, 'd MMM', locale=self._context.get('lang') or 'en_US')
            data.append({'label':label,'value':0.0, 'type': 'past' if i<0 else 'future'})

        # Build SQL query to find amount aggregated by week
        (select_sql_clause, query_args) = self._get_bar_graph_select_query()
        query = ''
        start_date = (first_day_of_week + timedelta(days=-7))
        for i in range(0,6):
            if i == 0:
                query += "("+select_sql_clause+" and invoice_date_due < '"+start_date.strftime(DF)+"')"
            elif i == 5:
                query += " UNION ALL ("+select_sql_clause+" and invoice_date_due >= '"+start_date.strftime(DF)+"')"
            else:
                next_date = start_date + timedelta(days=7)
                query += " UNION ALL ("+select_sql_clause+" and invoice_date_due >= '"+start_date.strftime(DF)+"' and invoice_date_due < '"+next_date.strftime(DF)+"')"
                start_date = next_date

        self.env.cr.execute(query, query_args)
        query_results = self.env.cr.dictfetchall()
        is_sample_data = True
        for index in range(0, len(query_results)):
            if query_results[index].get('aggr_date') != None:
                is_sample_data = False
                data[index]['value'] = query_results[index].get('total')

        [graph_title, graph_key] = self._graph_title_and_key()

        if is_sample_data:
            for index in range(0, len(query_results)):
                data[index]['type'] = 'o_sample_data'
                # we use unrealistic values for the sample data
                data[index]['value'] = random.randint(0, 20)
                graph_key = _('Sample data')

        return [{'values': data, 'title': graph_title, 'key': graph_key, 'is_sample_data': is_sample_data}]

    def _get_bar_graph_select_query(self):
        """
        Returns a tuple containing the base SELECT SQL query used to gather
        the bar graph's data as its first element, and the arguments dictionary
        for it as its second.
        """
        return ('''
            SELECT 
                SUM((CASE WHEN move.type IN ('out_refund', 'in_refund') THEN -1 else 1 END) * line.amount_residual) AS total,
                MIN(invoice_date_due) AS aggr_date
            FROM account_move_line line
            JOIN account_move move ON move.id = line.move_id
            WHERE move.journal_id = %(journal_id)s
            AND move.state = 'posted'
            AND move.invoice_payment_state = 'not_paid'
            AND move.type IN ('out_invoice', 'out_refund', 'in_invoice', 'in_refund', 'out_receipt', 'in_receipt')
        ''', {'journal_id': self.id})

    def get_journal_dashboard_datas(self):
        currency = self.currency_id or self.company_id.currency_id
        number_to_reconcile = number_to_check = last_balance = account_sum = 0
        title = ''
        number_draft = number_waiting = number_late = to_check_balance = 0
        sum_draft = sum_waiting = sum_late = 0.0
        if self.type in ['bank', 'cash']:
            last_bank_stmt = self.env['account.bank.statement'].search([('journal_id', 'in', self.ids)], order="date desc, id desc", limit=1)
            last_balance = last_bank_stmt and last_bank_stmt[0].balance_end or 0
            #Get the number of items to reconcile for that bank journal
            self.env.cr.execute("""SELECT COUNT(DISTINCT(line.id))
                            FROM account_bank_statement_line AS line
                            LEFT JOIN account_bank_statement AS st
                            ON line.statement_id = st.id
                            WHERE st.journal_id IN %s AND st.state = 'open' AND line.amount != 0.0 AND line.account_id IS NULL
                            AND not exists (select 1 from account_move_line aml where aml.statement_line_id = line.id)
                        """, (tuple(self.ids),))
            number_to_reconcile = self.env.cr.fetchone()[0]
            to_check_ids = self.to_check_ids()
            number_to_check = len(to_check_ids)
            to_check_balance = sum([r.amount for r in to_check_ids])
            # optimization to read sum of balance from account_move_line
            account_ids = tuple(ac for ac in [self.default_debit_account_id.id, self.default_credit_account_id.id] if ac)
            if account_ids:
                amount_field = 'aml.balance' if (not self.currency_id or self.currency_id == self.company_id.currency_id) else 'aml.amount_currency'
                query = """SELECT sum(%s) FROM account_move_line aml
                           LEFT JOIN account_move move ON aml.move_id = move.id
                           WHERE aml.account_id in %%s
                           AND move.date <= %%s AND move.state = 'posted';""" % (amount_field,)
                self.env.cr.execute(query, (account_ids, fields.Date.context_today(self),))
                query_results = self.env.cr.dictfetchall()
                if query_results and query_results[0].get('sum') != None:
                    account_sum = query_results[0].get('sum')
        #TODO need to check if all invoices are in the same currency than the journal!!!!
        elif self.type in ['sale', 'purchase']:
            title = _('Bills to pay') if self.type == 'purchase' else _('Invoices owed to you')

            (query, query_args) = self._get_open_bills_to_pay_query()
            self.env.cr.execute(query, query_args)
            query_results_to_pay = self.env.cr.dictfetchall()

            (query, query_args) = self._get_draft_bills_query()
            self.env.cr.execute(query, query_args)
            query_results_drafts = self.env.cr.dictfetchall()

            today = fields.Date.today()
            query = '''
                SELECT 
                    (CASE WHEN type IN ('out_refund', 'in_refund') THEN -1 ELSE 1 END) * amount_residual AS amount_total, 
                    currency_id AS currency,
                    type, 
                    invoice_date, 
                    company_id
                FROM account_move move
                WHERE journal_id = %s 
                AND date <= %s 
                AND state = 'posted'
                AND invoice_payment_state = 'not_paid'
                AND type IN ('out_invoice', 'out_refund', 'in_invoice', 'in_refund', 'out_receipt', 'in_receipt');
            '''
            self.env.cr.execute(query, (self.id, today))
            late_query_results = self.env.cr.dictfetchall()
            curr_cache = {}
            (number_waiting, sum_waiting) = self._count_results_and_sum_amounts(query_results_to_pay, currency, curr_cache=curr_cache)
            (number_draft, sum_draft) = self._count_results_and_sum_amounts(query_results_drafts, currency, curr_cache=curr_cache)
            (number_late, sum_late) = self._count_results_and_sum_amounts(late_query_results, currency, curr_cache=curr_cache)
            read = self.env['account.move'].read_group([('journal_id', '=', self.id), ('to_check', '=', True)], ['amount'], 'journal_id', lazy=False)
            if read:
                number_to_check = read[0]['__count']
                to_check_balance = read[0]['amount']

        difference = currency.round(last_balance-account_sum) + 0.0

        is_sample_data = self.kanban_dashboard_graph and any(data.get('is_sample_data', False) for data in json.loads(self.kanban_dashboard_graph))

        return {
            'number_to_check': number_to_check,
            'to_check_balance': formatLang(self.env, to_check_balance, currency_obj=currency),
            'number_to_reconcile': number_to_reconcile,
            'account_balance': formatLang(self.env, currency.round(account_sum) + 0.0, currency_obj=currency),
            'last_balance': formatLang(self.env, currency.round(last_balance) + 0.0, currency_obj=currency),
            'difference': formatLang(self.env, difference, currency_obj=currency) if difference else False,
            'number_draft': number_draft,
            'number_waiting': number_waiting,
            'number_late': number_late,
            'sum_draft': formatLang(self.env, currency.round(sum_draft) + 0.0, currency_obj=currency),
            'sum_waiting': formatLang(self.env, currency.round(sum_waiting) + 0.0, currency_obj=currency),
            'sum_late': formatLang(self.env, currency.round(sum_late) + 0.0, currency_obj=currency),
            'currency_id': currency.id,
            'bank_statements_source': self.bank_statements_source,
            'title': title,
            'is_sample_data': is_sample_data,
        }

    def _get_open_bills_to_pay_query(self):
        """
        Returns a tuple containing the SQL query used to gather the open bills
        data as its first element, and the arguments dictionary to use to run
        it as its second.
        """
        return ('''
            SELECT 
                (CASE WHEN move.type IN ('out_refund', 'in_refund') THEN -1 ELSE 1 END) * move.amount_residual AS amount_total, 
                move.currency_id AS currency, 
                move.type, 
                move.invoice_date, 
                move.company_id
            FROM account_move move
            WHERE move.journal_id = %(journal_id)s 
            AND move.state = 'posted'
            AND move.invoice_payment_state = 'not_paid'
            AND move.type IN ('out_invoice', 'out_refund', 'in_invoice', 'in_refund', 'out_receipt', 'in_receipt');
        ''', {'journal_id': self.id})

    def _get_draft_bills_query(self):
        """
        Returns a tuple containing as its first element the SQL query used to
        gather the bills in draft state data, and the arguments
        dictionary to use to run it as its second.
        """
        return ('''
            SELECT 
                (CASE WHEN move.type IN ('out_refund', 'in_refund') THEN -1 ELSE 1 END) * move.amount_total AS amount_total, 
                move.currency_id AS currency, 
                move.type, 
                move.invoice_date, 
                move.company_id
            FROM account_move move
            WHERE move.journal_id = %(journal_id)s 
            AND move.state = 'draft'
            AND move.invoice_payment_state = 'not_paid'
            AND move.type IN ('out_invoice', 'out_refund', 'in_invoice', 'in_refund', 'out_receipt', 'in_receipt');
        ''', {'journal_id': self.id})

    def _count_results_and_sum_amounts(self, results_dict, target_currency, curr_cache=None):
        """ Loops on a query result to count the total number of invoices and sum
        their amount_total field (expressed in the given target currency).
        amount_total must be signed !
        """
        rslt_count = 0
        rslt_sum = 0.0
        # Create a cache with currency rates to avoid unnecessary SQL requests. Do not copy
        # curr_cache on purpose, so the dictionary is modified and can be re-used for subsequent
        # calls of the method.
        curr_cache = {} if curr_cache is None else curr_cache
        for result in results_dict:
            cur = self.env['res.currency'].browse(result.get('currency'))
            company = self.env['res.company'].browse(result.get('company_id')) or self.env.company
            rslt_count += 1
            date = result.get('invoice_date') or fields.Date.today()

            amount = result.get('amount_total', 0) or 0
            if cur != target_currency:
                key = (cur, target_currency, company, date)
                # Using setdefault will call _get_conversion_rate, so we explicitly check the
                # existence of the key in the cache instead.
                if key not in curr_cache:
                    curr_cache[key] = self.env['res.currency']._get_conversion_rate(*key)
                amount *= curr_cache[key]
            rslt_sum += target_currency.round(amount)
        return (rslt_count, rslt_sum)

    def action_create_new(self):
        ctx = self._context.copy()
        ctx['default_journal_id'] = self.id
        if self.type == 'sale':
            ctx['default_type'] = 'out_refund' if ctx.get('refund') else 'out_invoice'
        elif self.type == 'purchase':
            ctx['default_type'] = 'in_refund' if ctx.get('refund') else 'in_invoice'
        else:
            ctx['default_type'] = 'entry'
            ctx['view_no_maturity'] = True
        return {
            'name': _('Create invoice/bill'),
            'type': 'ir.actions.act_window',
            'view_mode': 'form',
            'res_model': 'account.move',
            'view_id': self.env.ref('account.view_move_form').id,
            'context': ctx,
        }

    def create_cash_statement(self):
        ctx = self._context.copy()
        ctx.update({'journal_id': self.id, 'default_journal_id': self.id, 'default_journal_type': 'cash'})
        return {
            'name': _('Create cash statement'),
            'type': 'ir.actions.act_window',
            'view_mode': 'form',
            'res_model': 'account.bank.statement',
            'context': ctx,
        }

    def action_open_reconcile(self):
        if self.type in ['bank', 'cash']:
            # Open reconciliation view for bank statements belonging to this journal
            bank_stmt = self.env['account.bank.statement'].search([('journal_id', 'in', self.ids)]).mapped('line_ids')
            return {
                'type': 'ir.actions.client',
                'tag': 'bank_statement_reconciliation_view',
                'context': {'statement_line_ids': bank_stmt.ids, 'company_ids': self.mapped('company_id').ids},
            }
        else:
            # Open reconciliation view for customers/suppliers
            action_context = {'show_mode_selector': False, 'company_ids': self.mapped('company_id').ids}
            if self.type == 'sale':
                action_context.update({'mode': 'customers'})
            elif self.type == 'purchase':
                action_context.update({'mode': 'suppliers'})
            return {
                'type': 'ir.actions.client',
                'tag': 'manual_reconciliation_view',
                'context': action_context,
            }

    def action_open_to_check(self):
        self.ensure_one()
        ids = self.to_check_ids().ids
        action_context = {'show_mode_selector': False, 'company_ids': self.mapped('company_id').ids}
        action_context.update({'suspense_moves_mode': True})
        action_context.update({'statement_line_ids': ids})
        return {
            'type': 'ir.actions.client',
            'tag': 'bank_statement_reconciliation_view',
            'context': action_context,
        }

    def to_check_ids(self):
        self.ensure_one()
        domain = self.env['account.move.line']._get_suspense_moves_domain()
        domain.append(('journal_id', '=', self.id))
        statement_line_ids = self.env['account.move.line'].search(domain).mapped('statement_line_id')
        return statement_line_ids

    def open_action(self):
        """return action based on type for related journals"""
        action_name = self._context.get('action_name')

        # Find action based on journal.
        if not action_name:
            if self.type == 'bank':
                action_name = 'action_bank_statement_tree'
            elif self.type == 'cash':
                action_name = 'action_view_bank_statement_tree'
            elif self.type == 'sale':
                action_name = 'action_move_out_invoice_type'
            elif self.type == 'purchase':
                action_name = 'action_move_in_invoice_type'
            else:
                action_name = 'action_move_journal_line'

        # Set 'account.' prefix if missing.
        if '.' not in action_name:
            action_name = 'account.%s' % action_name

        action = self.env.ref(action_name).read()[0]
        context = self._context.copy()
        if 'context' in action and type(action['context']) == str:
            context.update(ast.literal_eval(action['context']))
        else:
            context.update(action.get('context', {}))
        action['context'] = context
        action['context'].update({
            'default_journal_id': self.id,
            'search_default_journal_id': self.id,
        })

        if self.type == 'sale':
            action['domain'] = [('type', 'in', ('out_invoice', 'out_refund', 'out_receipt'))]
        elif self.type == 'purchase':
            action['domain'] = [('type', 'in', ('in_invoice', 'in_refund', 'in_receipt'))]

        return action

    def open_spend_money(self):
        return self.open_payments_action('outbound')

    def open_collect_money(self):
        return self.open_payments_action('inbound')

    def open_transfer_money(self):
        return self.open_payments_action('transfer')

    def open_payments_action(self, payment_type, mode='tree'):
        if payment_type == 'outbound':
            action_ref = 'account.action_account_payments_payable'
        elif payment_type == 'transfer':
            action_ref = 'account.action_account_payments_transfer'
        else:
            action_ref = 'account.action_account_payments'
        [action] = self.env.ref(action_ref).read()
        action['context'] = dict(safe_eval(action.get('context')), default_journal_id=self.id, search_default_journal_id=self.id)
        if mode == 'form':
            action['views'] = [[False, 'form']]
        return action

    def open_action_with_context(self):
        action_name = self.env.context.get('action_name', False)
        if not action_name:
            return False
        ctx = dict(self.env.context, default_journal_id=self.id)
        if ctx.get('search_default_journal', False):
            ctx.update(search_default_journal_id=self.id)
        ctx.pop('group_by', None)
        ir_model_obj = self.env['ir.model.data']
        model, action_id = ir_model_obj.get_object_reference('account', action_name)
        [action] = self.env[model].browse(action_id).read()
        action['context'] = ctx
        if ctx.get('use_domain', False):
            action['domain'] = ['|', ('journal_id', '=', self.id), ('journal_id', '=', False)]
            action['name'] += ' for journal ' + self.name
        return action

    def create_bank_statement(self):
        """return action to create a bank statements. This button should be called only on journals with type =='bank'"""
        action = self.env.ref('account.action_bank_statement_tree').read()[0]
        action.update({
            'views': [[False, 'form']],
            'context': "{'default_journal_id': " + str(self.id) + "}",
        })
        return action

    def create_customer_payment(self):
        """return action to create a customer payment"""
        return self.open_payments_action('inbound', mode='form')

    def create_supplier_payment(self):
        """return action to create a supplier payment"""
        return self.open_payments_action('outbound', mode='form')

    def create_internal_transfer(self):
        """return action to create a internal transfer"""
        return self.open_payments_action('transfer', mode='form')

    #####################
    # Setup Steps Stuff #
    #####################
    def mark_bank_setup_as_done_action(self):
        """ Marks the 'bank setup' step as done in the setup bar and in the company."""
        self.company_id.set_onboarding_step_done('account_setup_bank_data_state')

    def unmark_bank_setup_as_done_action(self):
        """ Marks the 'bank setup' step as not done in the setup bar and in the company."""
        self.company_id.account_setup_bank_data_state = 'not_done'
