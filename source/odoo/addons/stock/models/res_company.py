# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.

from odoo import api, fields, models, _


class Company(models.Model):
    _inherit = "res.company"

    internal_transit_location_id = fields.Many2one(
        'stock.location', 'Internal Transit Location', ondelete="restrict",
        help="Technical field used for resupply routes between warehouses that belong to this company")

    def _create_transit_location(self):
        '''Create a transit location with company_id being the given company_id. This is needed
           in case of resuply routes between warehouses belonging to the same company, because
           we don't want to create accounting entries at that time.
        '''
        parent_location = self.env.ref('stock.stock_location_locations', raise_if_not_found=False)
        for company in self:
            location = self.env['stock.location'].create({
                'name': _('%s: Transit Location') % company.name,
                'usage': 'transit',
                'location_id': parent_location and parent_location.id or False,
                'company_id': company.id,
            })

            company.write({'internal_transit_location_id': location.id})

            company.partner_id.with_context(force_company=company.id).write({
                'property_stock_customer': location.id,
                'property_stock_supplier': location.id,
            })

    def _create_inventory_loss_location(self):
        parent_location = self.env.ref('stock.stock_location_locations_virtual', raise_if_not_found=False)
        inventory_loss_product_template_field = self.env['ir.model.fields'].search([('model','=','product.template'),('name','=','property_stock_inventory')])
        for company in self:
            inventory_loss_location = self.env['stock.location'].create({
                'name': '%s: Inventory adjustment' % company.name,
                'usage': 'inventory',
                'location_id': parent_location.id,
                'company_id': company.id,
            })
            self.env['ir.property'].create({
                'name': 'property_stock_inventory_%s' % company.name,
                'fields_id': inventory_loss_product_template_field.id,
                'company_id': company.id,
                'value': 'stock.location,%d' % inventory_loss_location.id,
            })

    def _create_production_location(self):
        parent_location = self.env.ref('stock.stock_location_locations_virtual', raise_if_not_found=False)
        production_product_template_field = self.env['ir.model.fields'].search([('model','=','product.template'),('name','=','property_stock_production')])
        for company in self:
            production_location = self.env['stock.location'].create({
                'name': '%s: Production' % company.name,
                'usage': 'production',
                'location_id': parent_location.id,
                'company_id': company.id,
            })
            self.env['ir.property'].create({
                'name': 'property_stock_inventory_%s' % company.name,
                'fields_id': production_product_template_field.id,
                'company_id': company.id,
                'value': 'stock.location,%d' % production_location.id,
            })

    def _create_scrap_location(self):
        for company in self:
            parent_location = self.env.ref('stock.stock_location_locations_virtual', raise_if_not_found=False)
            scrap_location = self.env['stock.location'].create({
                'name': '%s: Scrap' % company.name,
                'usage': 'inventory',
                'location_id': parent_location.id,
                'company_id': company.id,
                'scrap_location': True,
            })

    def _create_scrap_sequence(self):
        scrap_vals = []
        for company in self:
            scrap_vals.append({
                'name': '%s Sequence scrap' % company.name,
                'code': 'stock.scrap',
                'company_id': company.id,
                'prefix': 'SP/',
                'padding': 5,
                'number_next': 1,
                'number_increment': 1
            })
        if scrap_vals:
            self.env['ir.sequence'].create(scrap_vals)

    @api.model
    def create_missing_warehouse(self):
        """ This hook is used to add a warehouse on existing companies
        when module stock is installed.
        """
        company_ids  = self.env['res.company'].search([])
        company_with_warehouse = self.env['stock.warehouse'].search([]).mapped('company_id')
        company_without_warehouse = company_ids - company_with_warehouse
        for company in company_without_warehouse:
            self.env['stock.warehouse'].create({
                'name': company.name,
                'code': company.name[:5],
                'company_id': company.id,
                'partner_id': company.partner_id.id,
            })

    @api.model
    def create_missing_transit_location(self):
        company_without_transit = self.env['res.company'].search([('internal_transit_location_id', '=', False)])
        for company in company_without_transit:
            company._create_transit_location()

    @api.model
    def create_missing_inventory_loss_location(self):
        company_ids  = self.env['res.company'].search([])
        inventory_loss_product_template_field = self.env['ir.model.fields'].search([('model','=','product.template'),('name','=','property_stock_inventory')])
        companies_having_property = self.env['ir.property'].search([('fields_id', '=', inventory_loss_product_template_field.id)]).mapped('company_id')
        company_without_property = company_ids - companies_having_property
        for company in company_without_property:
            company._create_inventory_loss_location()

    @api.model
    def create_missing_production_location(self):
        company_ids  = self.env['res.company'].search([])
        production_product_template_field = self.env['ir.model.fields'].search([('model','=','product.template'),('name','=','property_stock_production')])
        companies_having_property = self.env['ir.property'].search([('fields_id', '=', production_product_template_field.id)]).mapped('company_id')
        company_without_property = company_ids - companies_having_property
        for company in company_without_property:
            company._create_production_location()

    @api.model
    def create_missing_scrap_location(self):
        company_ids  = self.env['res.company'].search([])
        companies_having_scrap_loc = self.env['stock.location'].search([('scrap_location', '=', True)]).mapped('company_id')
        company_without_property = company_ids - companies_having_scrap_loc
        for company in company_without_property:
            company._create_scrap_location()

    @api.model
    def create_missing_scrap_sequence(self):
        company_ids  = self.env['res.company'].search([])
        company_has_scrap_seq = self.env['ir.sequence'].search([('code', '=', 'stock.scrap')]).mapped('company_id')
        company_todo_sequence = company_ids - company_has_scrap_seq
        company_todo_sequence._create_scrap_sequence()

    def _create_per_company_locations(self):
        self.ensure_one()
        self._create_transit_location()
        self._create_inventory_loss_location()
        self._create_production_location()
        self._create_scrap_location()

    def _create_per_company_sequences(self):
        self.ensure_one()
        self._create_scrap_sequence()

    @api.model
    def create(self, vals):
        company = super(Company, self).create(vals)
        company.sudo()._create_per_company_locations()
        company.sudo()._create_per_company_sequences()
        self.env['stock.warehouse'].sudo().create({'name': company.name, 'code': company.name[:5], 'company_id': company.id, 'partner_id': company.partner_id.id})
        return company
