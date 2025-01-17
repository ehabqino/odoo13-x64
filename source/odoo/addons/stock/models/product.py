# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.

from odoo import api, fields, models, _
from odoo.exceptions import UserError
from odoo.osv import expression
from odoo.tools.float_utils import float_round
from datetime import datetime
import operator as py_operator

OPERATORS = {
    '<': py_operator.lt,
    '>': py_operator.gt,
    '<=': py_operator.le,
    '>=': py_operator.ge,
    '=': py_operator.eq,
    '!=': py_operator.ne
}

class Product(models.Model):
    _inherit = "product.product"

    stock_quant_ids = fields.One2many('stock.quant', 'product_id', help='Technical: used to compute quantities.')
    stock_move_ids = fields.One2many('stock.move', 'product_id', help='Technical: used to compute quantities.')
    qty_available = fields.Float(
        'Quantity On Hand', compute='_compute_quantities', search='_search_qty_available',
        digits='Product Unit of Measure',
        help="Current quantity of products.\n"
             "In a context with a single Stock Location, this includes "
             "goods stored at this Location, or any of its children.\n"
             "In a context with a single Warehouse, this includes "
             "goods stored in the Stock Location of this Warehouse, or any "
             "of its children.\n"
             "stored in the Stock Location of the Warehouse of this Shop, "
             "or any of its children.\n"
             "Otherwise, this includes goods stored in any Stock Location "
             "with 'internal' type.")
    virtual_available = fields.Float(
        'Forecast Quantity', compute='_compute_quantities', search='_search_virtual_available',
        digits='Product Unit of Measure',
        help="Forecast quantity (computed as Quantity On Hand "
             "- Outgoing + Incoming)\n"
             "In a context with a single Stock Location, this includes "
             "goods stored in this location, or any of its children.\n"
             "In a context with a single Warehouse, this includes "
             "goods stored in the Stock Location of this Warehouse, or any "
             "of its children.\n"
             "Otherwise, this includes goods stored in any Stock Location "
             "with 'internal' type.")
    incoming_qty = fields.Float(
        'Incoming', compute='_compute_quantities', search='_search_incoming_qty',
        digits='Product Unit of Measure',
        help="Quantity of planned incoming products.\n"
             "In a context with a single Stock Location, this includes "
             "goods arriving to this Location, or any of its children.\n"
             "In a context with a single Warehouse, this includes "
             "goods arriving to the Stock Location of this Warehouse, or "
             "any of its children.\n"
             "Otherwise, this includes goods arriving to any Stock "
             "Location with 'internal' type.")
    outgoing_qty = fields.Float(
        'Outgoing', compute='_compute_quantities', search='_search_outgoing_qty',
        digits='Product Unit of Measure',
        help="Quantity of planned outgoing products.\n"
             "In a context with a single Stock Location, this includes "
             "goods leaving this Location, or any of its children.\n"
             "In a context with a single Warehouse, this includes "
             "goods leaving the Stock Location of this Warehouse, or "
             "any of its children.\n"
             "Otherwise, this includes goods leaving any Stock "
             "Location with 'internal' type.")

    orderpoint_ids = fields.One2many('stock.warehouse.orderpoint', 'product_id', 'Minimum Stock Rules')
    nbr_reordering_rules = fields.Integer('Reordering Rules', compute='_compute_nbr_reordering_rules')
    reordering_min_qty = fields.Float(compute='_compute_nbr_reordering_rules')
    reordering_max_qty = fields.Float(compute='_compute_nbr_reordering_rules')
    putaway_rule_ids = fields.One2many('stock.putaway.rule', 'product_id', 'Putaway Rules')

    @api.depends('stock_move_ids.product_qty', 'stock_move_ids.state')
    def _compute_quantities(self):
        res = self._compute_quantities_dict(self._context.get('lot_id'), self._context.get('owner_id'), self._context.get('package_id'), self._context.get('from_date'), self._context.get('to_date'))
        for product in self:
            product.qty_available = res[product.id]['qty_available']
            product.incoming_qty = res[product.id]['incoming_qty']
            product.outgoing_qty = res[product.id]['outgoing_qty']
            product.virtual_available = res[product.id]['virtual_available']

    def _product_available(self, field_names=None, arg=False):
        """ Compatibility method """
        return self._compute_quantities_dict(self._context.get('lot_id'), self._context.get('owner_id'), self._context.get('package_id'), self._context.get('from_date'), self._context.get('to_date'))

    def _compute_quantities_dict(self, lot_id, owner_id, package_id, from_date=False, to_date=False):
        domain_quant_loc, domain_move_in_loc, domain_move_out_loc = self._get_domain_locations()
        domain_quant = [('product_id', 'in', self.ids)] + domain_quant_loc
        dates_in_the_past = False
        # only to_date as to_date will correspond to qty_available
        to_date = fields.Datetime.to_datetime(to_date)
        if to_date and to_date < fields.Datetime.now():
            dates_in_the_past = True

        domain_move_in = [('product_id', 'in', self.ids)] + domain_move_in_loc
        domain_move_out = [('product_id', 'in', self.ids)] + domain_move_out_loc
        if lot_id is not None:
            domain_quant += [('lot_id', '=', lot_id)]
        if owner_id is not None:
            domain_quant += [('owner_id', '=', owner_id)]
            domain_move_in += [('restrict_partner_id', '=', owner_id)]
            domain_move_out += [('restrict_partner_id', '=', owner_id)]
        if package_id is not None:
            domain_quant += [('package_id', '=', package_id)]
        if dates_in_the_past:
            domain_move_in_done = list(domain_move_in)
            domain_move_out_done = list(domain_move_out)
        if from_date:
            domain_move_in += [('date', '>=', from_date)]
            domain_move_out += [('date', '>=', from_date)]
        if to_date:
            domain_move_in += [('date', '<=', to_date)]
            domain_move_out += [('date', '<=', to_date)]

        Move = self.env['stock.move']
        Quant = self.env['stock.quant']
        domain_move_in_todo = [('state', 'in', ('waiting', 'confirmed', 'assigned', 'partially_available'))] + domain_move_in
        domain_move_out_todo = [('state', 'in', ('waiting', 'confirmed', 'assigned', 'partially_available'))] + domain_move_out
        moves_in_res = dict((item['product_id'][0], item['product_qty']) for item in Move.read_group(domain_move_in_todo, ['product_id', 'product_qty'], ['product_id'], orderby='id'))
        moves_out_res = dict((item['product_id'][0], item['product_qty']) for item in Move.read_group(domain_move_out_todo, ['product_id', 'product_qty'], ['product_id'], orderby='id'))
        quants_res = dict((item['product_id'][0], item['quantity']) for item in Quant.read_group(domain_quant, ['product_id', 'quantity'], ['product_id'], orderby='id'))
        if dates_in_the_past:
            # Calculate the moves that were done before now to calculate back in time (as most questions will be recent ones)
            domain_move_in_done = [('state', '=', 'done'), ('date', '>', to_date)] + domain_move_in_done
            domain_move_out_done = [('state', '=', 'done'), ('date', '>', to_date)] + domain_move_out_done
            moves_in_res_past = dict((item['product_id'][0], item['product_qty']) for item in Move.read_group(domain_move_in_done, ['product_id', 'product_qty'], ['product_id'], orderby='id'))
            moves_out_res_past = dict((item['product_id'][0], item['product_qty']) for item in Move.read_group(domain_move_out_done, ['product_id', 'product_qty'], ['product_id'], orderby='id'))

        res = dict()
        for product in self.with_context(prefetch_fields=False):
            product_id = product.id
            if not product_id:
                res[product_id] = dict.fromkeys(
                    ['qty_available', 'incoming_qty', 'outgoing_qty', 'virtual_available'],
                    0.0,
                )
                continue
            rounding = product.uom_id.rounding
            res[product_id] = {}
            if dates_in_the_past:
                qty_available = quants_res.get(product_id, 0.0) - moves_in_res_past.get(product_id, 0.0) + moves_out_res_past.get(product_id, 0.0)
            else:
                qty_available = quants_res.get(product_id, 0.0)
            res[product_id]['qty_available'] = float_round(qty_available, precision_rounding=rounding)
            res[product_id]['incoming_qty'] = float_round(moves_in_res.get(product_id, 0.0), precision_rounding=rounding)
            res[product_id]['outgoing_qty'] = float_round(moves_out_res.get(product_id, 0.0), precision_rounding=rounding)
            res[product_id]['virtual_available'] = float_round(
                qty_available + res[product_id]['incoming_qty'] - res[product_id]['outgoing_qty'],
                precision_rounding=rounding)

        return res

    def _get_description(self, picking_type_id):
        """ return product receipt/delivery/picking description depending on
        picking type passed as argument.
        """
        self.ensure_one()
        picking_code = picking_type_id.code
        description = self.description or self.name
        if picking_code == 'incoming':
            return self.description_pickingin or description
        if picking_code == 'outgoing':
            return self.description_pickingout or description
        if picking_code == 'internal':
            return self.description_picking or description

    def _get_domain_locations(self):
        '''
        Parses the context and returns a list of location_ids based on it.
        It will return all stock locations when no parameters are given
        Possible parameters are shop, warehouse, location, force_company, compute_child
        '''
        Warehouse = self.env['stock.warehouse']

        if self.env.context.get('company_owned', False):
            company_id = self.env.company.id
            return (
                [('location_id.company_id', '=', company_id), ('location_id.usage', 'in', ['internal', 'transit'])],
                [('location_id.company_id', '=', False), ('location_dest_id.company_id', '=', company_id)],
                [('location_id.company_id', '=', company_id), ('location_dest_id.company_id', '=', False),
            ])

        def _search_ids(model, values, force_company_id):
            ids = set()
            domain = []
            for item in values:
                if isinstance(item, int):
                    ids.add(item)
                else:
                    domain = expression.OR([[('name', 'ilike', item)], domain])
            if force_company_id:
                domain = expression.AND([[('company_id', '=', force_company_id)], domain])
            if domain:
                ids |= set(self.env[model].search(domain).ids)
            return ids

        # We may receive a location or warehouse from the context, either by explicit
        # python code or by the use of dummy fields in the search view.
        # Normalize them into a list.
        location = self.env.context.get('location')
        if location and not isinstance(location, list):
            location = [location]
        warehouse = self.env.context.get('warehouse')
        if warehouse and not isinstance(warehouse, list):
            warehouse = [warehouse]
        force_company = self.env.context.get('force_company', False)
        # filter by location and/or warehouse
        if warehouse:
            w_ids = set(Warehouse.browse(_search_ids('stock.warehouse', warehouse, force_company)).mapped('view_location_id').ids)
            if location:
                l_ids = _search_ids('stock.location', location, force_company)
                location_ids = w_ids & l_ids
            else:
                location_ids = w_ids
        else:
            if location:
                location_ids = _search_ids('stock.location', location, force_company)
            else:
                location_ids = set(Warehouse.search([]).mapped('view_location_id').ids)

        return self._get_domain_locations_new(location_ids, company_id=self.env.context.get('force_company', False), compute_child=self.env.context.get('compute_child', True))

    def _get_domain_locations_new(self, location_ids, company_id=False, compute_child=True):
        operator = compute_child and 'child_of' or 'in'
        domain = company_id and ['&', ('company_id', '=', company_id)] or []
        locations = self.env['stock.location'].browse(location_ids)
        # TDE FIXME: should move the support of child_of + auto_join directly in expression
        hierarchical_locations = locations if operator == 'child_of' else locations.browse()
        other_locations = locations - hierarchical_locations
        loc_domain = []
        dest_loc_domain = []
        # this optimizes [('location_id', 'child_of', hierarchical_locations.ids)]
        # by avoiding the ORM to search for children locations and injecting a
        # lot of location ids into the main query
        for location in hierarchical_locations:
            loc_domain = loc_domain and ['|'] + loc_domain or loc_domain
            loc_domain.append(('location_id.parent_path', '=like', location.parent_path + '%'))
            dest_loc_domain = dest_loc_domain and ['|'] + dest_loc_domain or dest_loc_domain
            dest_loc_domain.append(('location_dest_id.parent_path', '=like', location.parent_path + '%'))
        if other_locations:
            loc_domain = loc_domain and ['|'] + loc_domain or loc_domain
            loc_domain = loc_domain + [('location_id', operator, other_locations.ids)]
            dest_loc_domain = dest_loc_domain and ['|'] + dest_loc_domain or dest_loc_domain
            dest_loc_domain = dest_loc_domain + [('location_dest_id', operator, other_locations.ids)]
        usage = self._context.get('quantity_available_locations_domain')
        if usage:
            stock_loc_domain = expression.AND([domain + loc_domain, [('location_id.usage', 'in', usage)]])
        else:
            stock_loc_domain = domain + loc_domain
        return (
            stock_loc_domain,
            domain + dest_loc_domain + ['!'] + loc_domain if loc_domain else domain + dest_loc_domain,
            domain + loc_domain + ['!'] + dest_loc_domain if dest_loc_domain else domain + loc_domain
        )

    def _search_qty_available(self, operator, value):
        # In the very specific case we want to retrieve products with stock available, we only need
        # to use the quants, not the stock moves. Therefore, we bypass the usual
        # '_search_product_quantity' method and call '_search_qty_available_new' instead. This
        # allows better performances.
        if value == 0.0 and operator == '>' and not ({'from_date', 'to_date'} & set(self.env.context.keys())):
            product_ids = self._search_qty_available_new(
                operator, value, self.env.context.get('lot_id'), self.env.context.get('owner_id'),
                self.env.context.get('package_id')
            )
            return [('id', 'in', product_ids)]
        return self._search_product_quantity(operator, value, 'qty_available')

    def _search_virtual_available(self, operator, value):
        # TDE FIXME: should probably clean the search methods
        return self._search_product_quantity(operator, value, 'virtual_available')

    def _search_incoming_qty(self, operator, value):
        # TDE FIXME: should probably clean the search methods
        return self._search_product_quantity(operator, value, 'incoming_qty')

    def _search_outgoing_qty(self, operator, value):
        # TDE FIXME: should probably clean the search methods
        return self._search_product_quantity(operator, value, 'outgoing_qty')

    def _search_product_quantity(self, operator, value, field):
        # TDE FIXME: should probably clean the search methods
        # to prevent sql injections
        if field not in ('qty_available', 'virtual_available', 'incoming_qty', 'outgoing_qty'):
            raise UserError(_('Invalid domain left operand %s') % field)
        if operator not in ('<', '>', '=', '!=', '<=', '>='):
            raise UserError(_('Invalid domain operator %s') % operator)
        if not isinstance(value, (float, int)):
            raise UserError(_('Invalid domain right operand %s') % value)

        # TODO: Still optimization possible when searching virtual quantities
        ids = []
        # Order the search on `id` to prevent the default order on the product name which slows
        # down the search because of the join on the translation table to get the translated names.
        for product in self.with_context(prefetch_fields=False).search([], order='id'):
            if OPERATORS[operator](product[field], value):
                ids.append(product.id)
        return [('id', 'in', ids)]

    def _search_qty_available_new(self, operator, value, lot_id=False, owner_id=False, package_id=False):
        ''' Optimized method which doesn't search on stock.moves, only on stock.quants. '''
        product_ids = set()
        domain_quant = self._get_domain_locations()[0]
        if lot_id:
            domain_quant.append(('lot_id', '=', lot_id))
        if owner_id:
            domain_quant.append(('owner_id', '=', owner_id))
        if package_id:
            domain_quant.append(('package_id', '=', package_id))
        quants_groupby = self.env['stock.quant'].read_group(domain_quant, ['product_id', 'quantity'], ['product_id'], orderby='id')
        for quant in quants_groupby:
            if OPERATORS[operator](quant['quantity'], value):
                product_ids.add(quant['product_id'][0])
        return list(product_ids)

    def _compute_nbr_reordering_rules(self):
        read_group_res = self.env['stock.warehouse.orderpoint'].read_group(
            [('product_id', 'in', self.ids)],
            ['product_id', 'product_min_qty', 'product_max_qty'],
            ['product_id'])
        res = {i: {} for i in self.ids}
        for data in read_group_res:
            res[data['product_id'][0]]['nbr_reordering_rules'] = int(data['product_id_count'])
            res[data['product_id'][0]]['reordering_min_qty'] = data['product_min_qty']
            res[data['product_id'][0]]['reordering_max_qty'] = data['product_max_qty']
        for product in self:
            product_res = res.get(product.id) or {}
            product.nbr_reordering_rules = product_res.get('nbr_reordering_rules', 0)
            product.reordering_min_qty = product_res.get('reordering_min_qty', 0)
            product.reordering_max_qty = product_res.get('reordering_max_qty', 0)

    @api.onchange('tracking')
    def onchange_tracking(self):
        products = self.filtered(lambda self: self.tracking and self.tracking != 'none')
        if products:
            unassigned_quants = self.env['stock.quant'].search_count([('product_id', 'in', products.ids), ('lot_id', '=', False), ('location_id.usage','=', 'internal')])
            if unassigned_quants:
                return {
                    'warning': {
                        'title': _('Warning!'),
                        'message': _("You have products in stock that have no lot number.  You can assign serial numbers by doing an inventory.  ")}}

    @api.model
    def view_header_get(self, view_id, view_type):
        res = super(Product, self).view_header_get(view_id, view_type)
        if not res and self._context.get('active_id') and self._context.get('active_model') == 'stock.location':
            res = '%s%s' % (_('Products: '), self.env['stock.location'].browse(self._context['active_id']).name)
        return res

    @api.model
    def fields_view_get(self, view_id=None, view_type='form', toolbar=False, submenu=False):
        res = super(Product, self).fields_view_get(view_id=view_id, view_type=view_type, toolbar=toolbar, submenu=submenu)
        if self._context.get('location') and isinstance(self._context['location'], int):
            location = self.env['stock.location'].browse(self._context['location'])
            fields = res.get('fields')
            if fields:
                if location.usage == 'supplier':
                    if fields.get('virtual_available'):
                        res['fields']['virtual_available']['string'] = _('Future Receipts')
                    if fields.get('qty_available'):
                        res['fields']['qty_available']['string'] = _('Received Qty')
                elif location.usage == 'internal':
                    if fields.get('virtual_available'):
                        res['fields']['virtual_available']['string'] = _('Forecasted Quantity')
                elif location.usage == 'customer':
                    if fields.get('virtual_available'):
                        res['fields']['virtual_available']['string'] = _('Future Deliveries')
                    if fields.get('qty_available'):
                        res['fields']['qty_available']['string'] = _('Delivered Qty')
                elif location.usage == 'inventory':
                    if fields.get('virtual_available'):
                        res['fields']['virtual_available']['string'] = _('Future P&L')
                    if fields.get('qty_available'):
                        res['fields']['qty_available']['string'] = _('P&L Qty')
                elif location.usage == 'production':
                    if fields.get('virtual_available'):
                        res['fields']['virtual_available']['string'] = _('Future Productions')
                    if fields.get('qty_available'):
                        res['fields']['qty_available']['string'] = _('Produced Qty')
        return res

    def action_view_routes(self):
        return self.mapped('product_tmpl_id').action_view_routes()

    def action_view_stock_move_lines(self):
        self.ensure_one()
        action = self.env.ref('stock.stock_move_line_action').read()[0]
        action['domain'] = [('product_id', '=', self.id)]
        return action

    def action_view_related_putaway_rules(self):
        self.ensure_one()
        domain = [
            '|',
                ('product_id', '=', self.id),
                ('category_id', '=', self.product_tmpl_id.categ_id.id),
        ]
        return self.env['product.template']._get_action_view_related_putaway_rules(domain)

    def action_open_product_lot(self):
        self.ensure_one()
        action = self.env.ref('stock.action_production_lot_form').read()[0]
        action['domain'] = [('product_id', '=', self.id)]
        action['context'] = {'default_product_id': self.id}
        return action

    def action_open_quants(self):
        location_domain = self._get_domain_locations()[0]
        domain = expression.AND([[('product_id', 'in', self.ids)], location_domain])
        hide_location = not self.user_has_groups('stock.group_stock_multi_locations')
        hide_lot = all([product.tracking == 'none' for product in self])
        self = self.with_context(hide_location=hide_location, hide_lot=hide_lot)

        # If user have rights to write on quant, we define the view as editable.
        if self.user_has_groups('stock.group_stock_manager'):
            self = self.with_context(inventory_mode=True)
            # Set default location id if multilocations is inactive
            if not self.user_has_groups('stock.group_stock_multi_locations'):
                user_company = self.env.user.company_id
                warehouse = self.env['stock.warehouse'].search(
                    [('company_id', '=', user_company.id)], limit=1
                )
                if warehouse:
                    self = self.with_context(default_location_id=warehouse.lot_stock_id.id)
        # Set default product id if quants concern only one product
        if len(self) == 1:
            self = self.with_context(
                default_product_id=self.id,
                single_product=True
            )
        else:
            self = self.with_context(product_tmpl_id=self.product_tmpl_id.id)
        return self.env['stock.quant']._get_quants_action(domain)

    def action_update_quantity_on_hand(self):
        return self.product_tmpl_id.with_context({'default_product_id': self.id}).action_update_quantity_on_hand()

    @api.model
    def get_theoretical_quantity(self, product_id, location_id, lot_id=None, package_id=None, owner_id=None, to_uom=None):
        product_id = self.env['product.product'].browse(product_id)
        product_id.check_access_rights('read')
        product_id.check_access_rule('read')

        location_id = self.env['stock.location'].browse(location_id)
        lot_id = self.env['stock.production.lot'].browse(lot_id)
        package_id = self.env['stock.quant.package'].browse(package_id)
        owner_id = self.env['res.partner'].browse(owner_id)
        to_uom = self.env['uom.uom'].browse(to_uom)
        quants = self.env['stock.quant']._gather(product_id, location_id, lot_id=lot_id, package_id=package_id, owner_id=owner_id, strict=True)
        theoretical_quantity = sum([quant.quantity for quant in quants])
        if to_uom and product_id.uom_id != to_uom:
            theoretical_quantity = product_id.uom_id._compute_quantity(theoretical_quantity, to_uom)
        return theoretical_quantity

    def write(self, values):
        res = super(Product, self).write(values)
        if 'active' in values and not values['active']:
            products = self.mapped('orderpoint_ids').filtered(lambda r: r.active).mapped('product_id')
            if products:
                msg = _('You still have some active reordering rules on this product. Please archive or delete them first.')
                msg += '\n\n'
                for product in products:
                    msg += '- %s \n' % product.display_name
                raise UserError(msg)
        return res

class ProductTemplate(models.Model):
    _inherit = 'product.template'

    responsible_id = fields.Many2one(
        'res.users', string='Responsible', default=lambda self: self.env.uid,
        help="This user will be responsible of the next activities related to logistic operations for this product.")
    type = fields.Selection(selection_add=[('product', 'Storable Product')])
    property_stock_production = fields.Many2one(
        'stock.location', "Production Location",
        company_dependent=True, domain=[('usage', 'like', 'production')],
        help="This stock location will be used, instead of the default one, as the source location for stock moves generated by manufacturing orders.")
    property_stock_inventory = fields.Many2one(
        'stock.location', "Inventory Location",
        company_dependent=True, domain=[('usage', 'like', 'inventory')],
        help="This stock location will be used, instead of the default one, as the source location for stock moves generated when you do an inventory.")
    sale_delay = fields.Float(
        'Customer Lead Time', default=0,
        help="Delivery lead time, in days. It's the number of days, promised to the customer, between the confirmation of the sales order and the delivery.")
    tracking = fields.Selection([
        ('serial', 'By Unique Serial Number'),
        ('lot', 'By Lots'),
        ('none', 'No Tracking')], string="Tracking", help="Ensure the traceability of a storable product in your warehouse.", default='none', required=True)
    description_picking = fields.Text('Description on Picking', translate=True)
    description_pickingout = fields.Text('Description on Delivery Orders', translate=True)
    description_pickingin = fields.Text('Description on Receptions', translate=True)
    qty_available = fields.Float(
        'Quantity On Hand', compute='_compute_quantities', search='_search_qty_available',
        digits='Product Unit of Measure')
    virtual_available = fields.Float(
        'Forecasted Quantity', compute='_compute_quantities', search='_search_virtual_available',
        digits='Product Unit of Measure')
    incoming_qty = fields.Float(
        'Incoming', compute='_compute_quantities', search='_search_incoming_qty',
        digits='Product Unit of Measure')
    outgoing_qty = fields.Float(
        'Outgoing', compute='_compute_quantities', search='_search_outgoing_qty',
        digits='Product Unit of Measure')
    # The goal of these fields is not to be able to search a location_id/warehouse_id but
    # to properly make these fields "dummy": only used to put some keys in context from
    # the search view in order to influence computed field
    location_id = fields.Many2one('stock.location', 'Location', store=False, search=lambda operator, operand, vals: [])
    warehouse_id = fields.Many2one('stock.warehouse', 'Warehouse', store=False, search=lambda operator, operand, vals: [])
    route_ids = fields.Many2many(
        'stock.location.route', 'stock_route_product', 'product_id', 'route_id', 'Routes',
        domain=[('product_selectable', '=', True)],
        help="Depending on the modules installed, this will allow you to define the route of the product: whether it will be bought, manufactured, replenished on order, etc.")
    nbr_reordering_rules = fields.Integer('Reordering Rules', compute='_compute_nbr_reordering_rules')
    # TDE FIXME: really used ?
    reordering_min_qty = fields.Float(compute='_compute_nbr_reordering_rules')
    reordering_max_qty = fields.Float(compute='_compute_nbr_reordering_rules')
    # TDE FIXME: seems only visible in a view - remove me ?
    route_from_categ_ids = fields.Many2many(
        relation="stock.location.route", string="Category Routes",
        related='categ_id.total_route_ids', readonly=False)

    def _is_cost_method_standard(self):
        return True

    @api.depends(
        'product_variant_ids',
        'product_variant_ids.stock_move_ids.product_qty',
        'product_variant_ids.stock_move_ids.state',
    )
    def _compute_quantities(self):
        res = self._compute_quantities_dict()
        for template in self:
            template.qty_available = res[template.id]['qty_available']
            template.virtual_available = res[template.id]['virtual_available']
            template.incoming_qty = res[template.id]['incoming_qty']
            template.outgoing_qty = res[template.id]['outgoing_qty']

    def _product_available(self, name, arg):
        return self._compute_quantities_dict()

    def _compute_quantities_dict(self):
        # TDE FIXME: why not using directly the function fields ?
        variants_available = self.mapped('product_variant_ids')._product_available()
        prod_available = {}
        for template in self:
            qty_available = 0
            virtual_available = 0
            incoming_qty = 0
            outgoing_qty = 0
            for p in template.product_variant_ids:
                qty_available += variants_available[p.id]["qty_available"]
                virtual_available += variants_available[p.id]["virtual_available"]
                incoming_qty += variants_available[p.id]["incoming_qty"]
                outgoing_qty += variants_available[p.id]["outgoing_qty"]
            prod_available[template.id] = {
                "qty_available": qty_available,
                "virtual_available": virtual_available,
                "incoming_qty": incoming_qty,
                "outgoing_qty": outgoing_qty,
            }
        return prod_available

    @api.model
    def _get_action_view_related_putaway_rules(self, domain):
        return {
            'name': _('Putaway Rules'),
            'type': 'ir.actions.act_window',
            'res_model': 'stock.putaway.rule',
            'view_mode': 'list',
            'domain': domain,
        }

    def _search_qty_available(self, operator, value):
        domain = [('qty_available', operator, value)]
        product_variant_ids = self.env['product.product'].search(domain)
        return [('product_variant_ids', 'in', product_variant_ids.ids)]

    def _search_virtual_available(self, operator, value):
        domain = [('virtual_available', operator, value)]
        product_variant_ids = self.env['product.product'].search(domain)
        return [('product_variant_ids', 'in', product_variant_ids.ids)]

    def _search_incoming_qty(self, operator, value):
        domain = [('incoming_qty', operator, value)]
        product_variant_ids = self.env['product.product'].search(domain)
        return [('product_variant_ids', 'in', product_variant_ids.ids)]

    def _search_outgoing_qty(self, operator, value):
        domain = [('outgoing_qty', operator, value)]
        product_variant_ids = self.env['product.product'].search(domain)
        return [('product_variant_ids', 'in', product_variant_ids.ids)]

    def _compute_nbr_reordering_rules(self):
        res = {k: {'nbr_reordering_rules': 0, 'reordering_min_qty': 0, 'reordering_max_qty': 0} for k in self.ids}
        product_data = self.env['stock.warehouse.orderpoint'].read_group([('product_id.product_tmpl_id', 'in', self.ids)], ['product_id', 'product_min_qty', 'product_max_qty'], ['product_id'])
        for data in product_data:
            product = self.env['product.product'].browse([data['product_id'][0]])
            product_tmpl_id = product.product_tmpl_id.id
            res[product_tmpl_id]['nbr_reordering_rules'] += int(data['product_id_count'])
            res[product_tmpl_id]['reordering_min_qty'] = data['product_min_qty']
            res[product_tmpl_id]['reordering_max_qty'] = data['product_max_qty']
        for template in self:
            if not template.id:
                template.nbr_reordering_rules = 0
                template.reordering_min_qty = 0
                template.reordering_max_qty = 0
                continue
            template.nbr_reordering_rules = res[template.id]['nbr_reordering_rules']
            template.reordering_min_qty = res[template.id]['reordering_min_qty']
            template.reordering_max_qty = res[template.id]['reordering_max_qty']

    @api.onchange('tracking')
    def onchange_tracking(self):
        return self.mapped('product_variant_ids').onchange_tracking()

    @api.onchange('type')
    def _onchange_type(self):
        res = super(ProductTemplate, self)._onchange_type()
        if self.type == 'consu' and self.tracking != 'none':
            self.tracking = 'none'
        return res

    def write(self, vals):
        if 'uom_id' in vals:
            new_uom = self.env['uom.uom'].browse(vals['uom_id'])
            updated = self.filtered(lambda template: template.uom_id != new_uom)
            done_moves = self.env['stock.move'].search([('product_id', 'in', updated.with_context(active_test=False).mapped('product_variant_ids').ids)], limit=1)
            if done_moves:
                raise UserError(_("You cannot change the unit of measure as there are already stock moves for this product. If you want to change the unit of measure, you should rather archive this product and create a new one."))
        if 'type' in vals and vals['type'] != 'product' and sum(self.mapped('nbr_reordering_rules')) != 0:
            raise UserError(_('You still have some active reordering rules on this product. Please archive or delete them first.'))
        if any('type' in vals and vals['type'] != prod_tmpl.type for prod_tmpl in self):
            existing_move_lines = self.env['stock.move.line'].search([
                ('product_id', 'in', self.mapped('product_variant_ids').ids),
                ('state', 'in', ['partially_available', 'assigned']),
            ])
            if existing_move_lines:
                raise UserError(_("You can not change the type of a product that is currently reserved on a stock move. If you need to change the type, you should first unreserve the stock move."))
        return super(ProductTemplate, self).write(vals)

    def action_open_quants(self):
        return self.product_variant_ids.action_open_quants()

    def action_update_quantity_on_hand(self):
        advanced_option_groups = [
            'stock.group_stock_multi_locations',
            'stock.group_production_lot',
            'stock.group_tracking_owner',
            'product.group_stock_packaging'
        ]
        if (self.env.user.user_has_groups(','.join(advanced_option_groups))):
            return self.action_open_quants()
        else:
            default_product_id = len(self.product_variant_ids) == 1 and self.product_variant_id.id
            action = self.env.ref('stock.action_change_product_quantity').read()[0]
            action['context'] = dict(
                self.env.context,
                default_product_id=default_product_id,
                default_product_tmpl_id=self.id
            )
            return action

    def action_view_related_putaway_rules(self):
        self.ensure_one()
        domain = [
            '|',
                ('product_id.product_tmpl_id', '=', self.id),
                ('category_id', '=', self.categ_id.id),
        ]
        return self._get_action_view_related_putaway_rules(domain)

    def action_view_orderpoints(self):
        products = self.mapped('product_variant_ids')
        action = self.env.ref('stock.product_open_orderpoint').read()[0]
        if products and len(products) == 1:
            action['context'] = {'default_product_id': products.ids[0], 'search_default_product_id': products.ids[0]}
        else:
            action['domain'] = [('product_id', 'in', products.ids)]
            action['context'] = {}
        return action

    def action_view_stock_move_lines(self):
        self.ensure_one()
        action = self.env.ref('stock.stock_move_line_action').read()[0]
        action['domain'] = [('product_id.product_tmpl_id', 'in', self.ids)]
        return action

    def action_open_product_lot(self):
        self.ensure_one()
        action = self.env.ref('stock.action_production_lot_form').read()[0]
        action['domain'] = [('product_id.product_tmpl_id', '=', self.id)]
        if self.product_variant_count == 1:
            action['context'] = {
                'default_product_id': self.product_variant_id.id,
            }

        return action

class ProductCategory(models.Model):
    _inherit = 'product.category'

    route_ids = fields.Many2many(
        'stock.location.route', 'stock_location_route_categ', 'categ_id', 'route_id', 'Routes',
        domain=[('product_categ_selectable', '=', True)])
    removal_strategy_id = fields.Many2one(
        'product.removal', 'Force Removal Strategy',
        help="Set a specific removal strategy that will be used regardless of the source location for this product category")
    total_route_ids = fields.Many2many(
        'stock.location.route', string='Total routes', compute='_compute_total_route_ids',
        readonly=True)
    putaway_rule_ids = fields.One2many('stock.putaway.rule', 'category_id', 'Putaway Rules')

    def _compute_total_route_ids(self):
        for category in self:
            base_cat = category
            routes = category.route_ids
            while base_cat.parent_id:
                base_cat = base_cat.parent_id
                routes |= base_cat.route_ids
            category.total_route_ids = routes


class UoM(models.Model):
    _inherit = 'uom.uom'

    def write(self, values):
        # Users can not update the factor if open stock moves are based on it
        if 'factor' in values or 'factor_inv' in values or 'category_id' in values:
            changed = self.filtered(
                lambda u: any(u[f] != values[f] if f in values else False
                              for f in {'factor', 'factor_inv'})) + self.filtered(
                lambda u: any(u[f].id != int(values[f]) if f in values else False
                              for f in {'category_id'}))
            if changed:
                stock_move_lines = self.env['stock.move.line'].search_count([
                    ('product_uom_id.category_id', 'in', changed.mapped('category_id.id')),
                    ('state', '!=', 'cancel'),
                ])

                if stock_move_lines:
                    raise UserError(_(
                        "You cannot change the ratio of this unit of mesure as some"
                        " products with this UoM have already been moved or are "
                        "currently reserved."
                    ))
        return super(UoM, self).write(values)

    def _adjust_uom_quantities(self, qty, quant_uom):
        """ This method adjust the quantities of a procurement if its UoM isn't the same
        as the one of the quant and the parameter 'propagate_uom' is not set.
        """
        procurement_uom = self
        computed_qty = qty
        get_param = self.env['ir.config_parameter'].sudo().get_param
        if procurement_uom.id != quant_uom.id and get_param('stock.propagate_uom') != '1':
            computed_qty = self._compute_quantity(qty, quant_uom, rounding_method='HALF-UP')
            procurement_uom = quant_uom
        return (computed_qty, procurement_uom)
