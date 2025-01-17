# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.

import datetime

from odoo import api, fields, models
from odoo.tools.float_utils import float_round


class HrEmployeeBase(models.AbstractModel):
    _inherit = "hr.employee.base"

    def _group_hr_user_domain(self):
        group = self.env.ref('hr_holidays.group_hr_holidays_team_leader', raise_if_not_found=False)
        return [('groups_id', 'in', group.ids)] if group else []

    leave_manager_id = fields.Many2one(
        'res.users', string='Time Off Responsible',
        domain=_group_hr_user_domain,
        help="User responsible of leaves approval. Should be Team Leader or Department Manager.")
    remaining_leaves = fields.Float(
        compute='_compute_remaining_leaves', string='Remaining Paid Time Off',
        help='Total number of paid time off allocated to this employee, change this value to create allocation/time off request. '
             'Total based on all the time off types without overriding limit.')
    current_leave_state = fields.Selection(compute='_compute_leave_status', string="Current Time Off Status",
        selection=[
            ('draft', 'New'),
            ('confirm', 'Waiting Approval'),
            ('refuse', 'Refused'),
            ('validate1', 'Waiting Second Approval'),
            ('validate', 'Approved'),
            ('cancel', 'Cancelled')
        ])
    current_leave_id = fields.Many2one('hr.leave.type', compute='_compute_leave_status', string="Current Time Off Type")
    leave_date_from = fields.Date('From Date', compute='_compute_leave_status')
    leave_date_to = fields.Date('To Date', compute='_compute_leave_status')
    leaves_count = fields.Float('Number of Time Off', compute='_compute_remaining_leaves')
    allocation_count = fields.Float('Total number of days allocated.', compute='_compute_allocation_count')
    allocation_used_count = fields.Float('Total number of days off used', compute='_compute_total_allocation_used')
    show_leaves = fields.Boolean('Able to see Remaining Time Off', compute='_compute_show_leaves')
    is_absent = fields.Boolean('Absent Today', compute='_compute_leave_status', search='_search_absent_employee')

    def _get_date_start_work(self):
        return self.create_date

    def _get_remaining_leaves(self):
        """ Helper to compute the remaining leaves for the current employees
            :returns dict where the key is the employee id, and the value is the remain leaves
        """
        self._cr.execute("""
            SELECT
                sum(h.number_of_days) AS days,
                h.employee_id
            FROM
                (
                    SELECT holiday_status_id, number_of_days,
                        state, employee_id
                    FROM hr_leave_allocation
                    UNION ALL
                    SELECT holiday_status_id, (number_of_days * -1) as number_of_days,
                        state, employee_id
                    FROM hr_leave
                ) h
                join hr_leave_type s ON (s.id=h.holiday_status_id)
            WHERE
                h.state='validate' AND
                (s.allocation_type='fixed' OR s.allocation_type='fixed_allocation') AND
                h.employee_id in %s
            GROUP BY h.employee_id""", (tuple(self.ids),))
        return dict((row['employee_id'], row['days']) for row in self._cr.dictfetchall())

    def _compute_remaining_leaves(self):
        remaining = self._get_remaining_leaves()
        for employee in self:
            value = float_round(remaining.get(employee.id, 0.0), precision_digits=2)
            employee.leaves_count = value
            employee.remaining_leaves = value

    def _compute_allocation_count(self):
        for employee in self:
            allocations = self.env['hr.leave.allocation'].search([
                ('employee_id', '=', employee.id),
                ('state', '=', 'validate'),
                '|',
                    ('date_to', '=', False),
                    ('date_to', '>=', datetime.date.today()),
            ])
            employee.allocation_count = sum(allocations.mapped('number_of_days'))

    def _compute_total_allocation_used(self):
        for employee in self:
            employee.allocation_used_count = employee.allocation_count - employee.remaining_leaves

    def _compute_presence_state(self):
        super()._compute_presence_state()
        employees = self.filtered(lambda employee: employee.hr_presence_state != 'present' and employee.is_absent)
        employees.update({'hr_presence_state': 'absent'})

    def _compute_leave_status(self):
        # Used SUPERUSER_ID to forcefully get status of other user's leave, to bypass record rule
        holidays = self.env['hr.leave'].sudo().search([
            ('employee_id', 'in', self.ids),
            ('date_from', '<=', fields.Datetime.now()),
            ('date_to', '>=', fields.Datetime.now()),
            ('state', 'not in', ('cancel', 'refuse'))
        ])
        leave_data = {}
        for holiday in holidays:
            leave_data[holiday.employee_id.id] = {}
            leave_data[holiday.employee_id.id]['leave_date_from'] = holiday.date_from.date()
            leave_data[holiday.employee_id.id]['leave_date_to'] = holiday.date_to.date()
            leave_data[holiday.employee_id.id]['current_leave_state'] = holiday.state
            leave_data[holiday.employee_id.id]['current_leave_id'] = holiday.holiday_status_id.id

        for employee in self:
            employee.leave_date_from = leave_data.get(employee.id, {}).get('leave_date_from')
            employee.leave_date_to = leave_data.get(employee.id, {}).get('leave_date_to')
            employee.current_leave_state = leave_data.get(employee.id, {}).get('current_leave_state')
            employee.current_leave_id = leave_data.get(employee.id, {}).get('current_leave_id')
            employee.is_absent = leave_data.get(employee.id) and leave_data.get(employee.id, {}).get('current_leave_state') not in ['cancel', 'refuse', 'draft']

    @api.onchange('parent_id')
    def _onchange_parent_id(self):
        super(HrEmployeeBase, self)._onchange_parent_id()
        previous_manager = self._origin.parent_id.user_id
        manager = self.parent_id.user_id
        if manager and manager.has_group('hr.group_hr_user') and (self.leave_manager_id == previous_manager or not self.leave_manager_id):
            self.leave_manager_id = manager

    def _compute_show_leaves(self):
        show_leaves = self.env['res.users'].has_group('hr_holidays.group_hr_holidays_user')
        for employee in self:
            if show_leaves or employee.user_id == self.env.user:
                employee.show_leaves = True
            else:
                employee.show_leaves = False

    def _search_absent_employee(self, operator, value):
        holidays = self.env['hr.leave'].sudo().search([
            ('employee_id', '!=', False),
            ('state', 'not in', ['cancel', 'refuse']),
            ('date_from', '<=', datetime.datetime.utcnow()),
            ('date_to', '>=', datetime.datetime.utcnow())
        ])
        return [('id', 'in', holidays.mapped('employee_id').ids)]

    def write(self, values):
        res = super(HrEmployeeBase, self).write(values)
        today_date = fields.Datetime.now()
        if 'parent_id' in values or 'department_id' in values:
            hr_vals = {}
            if values.get('parent_id') is not None:
                hr_vals['manager_id'] = values['parent_id']
            if values.get('department_id') is not None:
                hr_vals['department_id'] = values['department_id']
            holidays = self.env['hr.leave'].sudo().search(['|', ('state', 'in', ['draft', 'confirm']), ('date_from', '>', today_date), ('employee_id', 'in', self.ids)])
            holidays.write(hr_vals)
            allocations = self.env['hr.leave.allocation'].sudo().search([('state', 'in', ['draft', 'confirm']), ('employee_id', 'in', self.ids)])
            allocations.write(hr_vals)
        return res
