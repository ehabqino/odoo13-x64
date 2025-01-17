# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.

""" Implementation of "INVENTORY VALUATION TESTS (With valuation layers)" spreadsheet. """

from odoo.tests import Form, tagged
from odoo.tests.common import SavepointCase, TransactionCase


class TestStockValuationCommon(SavepointCase):
    @classmethod
    def setUpClass(cls):
        super(TestStockValuationCommon, cls).setUpClass()
        cls.stock_location = cls.env.ref('stock.stock_location_stock')
        cls.customer_location = cls.env.ref('stock.stock_location_customers')
        cls.supplier_location = cls.env.ref('stock.stock_location_suppliers')
        cls.uom_unit = cls.env.ref('uom.product_uom_unit')
        cls.product1 = cls.env['product.product'].create({
            'name': 'product1',
            'type': 'product',
            'categ_id': cls.env.ref('product.product_category_all').id,
        })
        cls.picking_type_in = cls.env.ref('stock.picking_type_in')
        cls.picking_type_out = cls.env.ref('stock.picking_type_out')

    def setUp(self):
        super(TestStockValuationCommon, self).setUp()
        # Counter automatically incremented by `_make_in_move` and `_make_out_move`.
        self.days = 0

    def _make_in_move(self, product, quantity, unit_cost=None, create_picking=False):
        """ Helper to create and validate a receipt move.
        """
        unit_cost = unit_cost or product.standard_price
        in_move = self.env['stock.move'].create({
            'name': 'in %s units @ %s per unit' % (str(quantity), str(unit_cost)),
            'product_id': product.id,
            'location_id': self.supplier_location.id,
            'location_dest_id': self.stock_location.id,
            'product_uom': self.uom_unit.id,
            'product_uom_qty': quantity,
            'price_unit': unit_cost,
            'picking_type_id': self.picking_type_in.id,
        })

        if create_picking:
            picking = self.env['stock.picking'].create({
                'picking_type_id': in_move.picking_type_id.id,
                'location_id': in_move.location_id.id,
                'location_dest_id': in_move.location_dest_id.id,
            })
            in_move.write({'picking_id': picking.id})

        in_move._action_confirm()
        in_move._action_assign()
        in_move.move_line_ids.qty_done = quantity
        in_move._action_done()

        self.days += 1
        return in_move.with_context(svl=True)

    def _make_out_move(self, product, quantity, force_assign=None, create_picking=False):
        """ Helper to create and validate a delivery move.
        """
        out_move = self.env['stock.move'].create({
            'name': 'out %s units' % str(quantity),
            'product_id': product.id,
            'location_id': self.stock_location.id,
            'location_dest_id': self.customer_location.id,
            'product_uom': self.uom_unit.id,
            'product_uom_qty': quantity,
            'picking_type_id': self.picking_type_out.id,
        })

        if create_picking:
            picking = self.env['stock.picking'].create({
                'picking_type_id': out_move.picking_type_id.id,
                'location_id': out_move.location_id.id,
                'location_dest_id': out_move.location_dest_id.id,
            })
            out_move.write({'picking_id': picking.id})

        out_move._action_confirm()
        out_move._action_assign()
        if force_assign:
            self.env['stock.move.line'].create({
                'move_id': out_move.id,
                'product_id': out_move.product_id.id,
                'product_uom_id': out_move.product_uom.id,
                'location_id': out_move.location_id.id,
                'location_dest_id': out_move.location_dest_id.id,
            })
        out_move.move_line_ids.qty_done = quantity
        out_move._action_done()

        self.days += 1
        return out_move.with_context(svl=True)

    def _make_dropship_move(self, product, quantity, unit_cost=None):
        dropshipped = self.env['stock.move'].create({
            'name': 'dropship %s units' % str(quantity),
            'product_id': product.id,
            'location_id': self.supplier_location.id,
            'location_dest_id': self.customer_location.id,
            'product_uom': self.uom_unit.id,
            'product_uom_qty': quantity,
            'picking_type_id': self.picking_type_out.id,
        })
        if unit_cost:
            dropshipped.unit_cost = unit_cost
        dropshipped._action_confirm()
        dropshipped._action_assign()
        dropshipped.move_line_ids.qty_done = quantity
        dropshipped._action_done()
        return dropshipped

    def _make_return(self, move, quantity_to_return):
        stock_return_picking = Form(self.env['stock.return.picking']\
            .with_context(active_ids=[move.picking_id.id], active_id=move.picking_id.id, active_model='stock.picking'))
        stock_return_picking = stock_return_picking.save()
        stock_return_picking.product_return_moves.quantity = quantity_to_return
        stock_return_picking_action = stock_return_picking.create_returns()
        return_pick = self.env['stock.picking'].browse(stock_return_picking_action['res_id'])
        return_pick.move_lines[0].move_line_ids[0].qty_done = quantity_to_return
        return_pick.action_done()
        return return_pick.move_lines


class TestStockValuationStandard(TestStockValuationCommon):
    def setUp(self):
        super(TestStockValuationStandard, self).setUp()
        self.product1.product_tmpl_id.categ_id.property_cost_method = 'standard'
        self.product1.product_tmpl_id.standard_price = 10

    def test_normal_1(self):
        self.product1.product_tmpl_id.categ_id.property_valuation = 'manual_periodic'

        move1 = self._make_in_move(self.product1, 10)
        move2 = self._make_in_move(self.product1, 10)
        move3 = self._make_out_move(self.product1, 15)

        self.assertEqual(self.product1.value_svl, 50)
        self.assertEqual(self.product1.quantity_svl, 5)

    def test_change_in_past_increase_in_1(self):
        self.product1.product_tmpl_id.categ_id.property_valuation = 'manual_periodic'

        move1 = self._make_in_move(self.product1, 10)
        move2 = self._make_in_move(self.product1, 10)
        move3 = self._make_out_move(self.product1, 15)
        move1.move_line_ids.qty_done = 15

        self.assertEqual(self.product1.value_svl, 100)
        self.assertEqual(self.product1.quantity_svl, 10)

    def test_change_in_past_decrease_in_1(self):
        self.product1.product_tmpl_id.categ_id.property_valuation = 'manual_periodic'

        move1 = self._make_in_move(self.product1, 10)
        move2 = self._make_in_move(self.product1, 10)
        move3 = self._make_out_move(self.product1, 15)
        move1.move_line_ids.qty_done = 5

        self.assertEqual(self.product1.value_svl, 0)
        self.assertEqual(self.product1.quantity_svl, 0)

    def test_change_in_past_add_ml_in_1(self):
        self.product1.product_tmpl_id.categ_id.property_valuation = 'manual_periodic'

        move1 = self._make_in_move(self.product1, 10)
        move2 = self._make_in_move(self.product1, 10)
        move3 = self._make_out_move(self.product1, 15)
        self.env['stock.move.line'].create({
            'move_id': move1.id,
            'product_id': move1.product_id.id,
            'qty_done': 5,
            'product_uom_id': move1.product_uom.id,
            'location_id': move1.location_id.id,
            'location_dest_id': move1.location_dest_id.id,
        })

        self.assertEqual(self.product1.value_svl, 100)
        self.assertEqual(self.product1.quantity_svl, 10)

    def test_change_in_past_increase_out_1(self):
        self.product1.product_tmpl_id.categ_id.property_valuation = 'manual_periodic'

        move1 = self._make_in_move(self.product1, 10)
        move2 = self._make_out_move(self.product1, 1)
        move2.move_line_ids.qty_done = 5

        self.assertEqual(self.product1.value_svl, 50)
        self.assertEqual(self.product1.quantity_svl, 5)

    def test_change_in_past_decrease_out_1(self):
        self.product1.product_tmpl_id.categ_id.property_valuation = 'manual_periodic'

        move1 = self._make_in_move(self.product1, 10)
        move2 = self._make_out_move(self.product1, 5)
        move2.move_line_ids.qty_done = 1

        self.assertEqual(self.product1.value_svl, 90)
        self.assertEqual(self.product1.quantity_svl, 9)

    def test_change_standard_price_1(self):
        self.product1.product_tmpl_id.categ_id.property_valuation = 'manual_periodic'

        move1 = self._make_in_move(self.product1, 10)
        move2 = self._make_in_move(self.product1, 10)
        move3 = self._make_out_move(self.product1, 15)

        # change cost from 10 to 15
        self.product1._change_standard_price(15.0)

        self.assertEqual(self.product1.value_svl, 75)
        self.assertEqual(self.product1.quantity_svl, 5)
        self.assertEqual(self.product1.stock_valuation_layer_ids[-1].description, 'Product value manually modified (from 10.0 to 15.0)')

    def test_negative_1(self):
        self.product1.product_tmpl_id.categ_id.property_valuation = 'manual_periodic'

        move1 = self._make_in_move(self.product1, 10)
        move2 = self._make_out_move(self.product1, 15)
        self.env['stock.move.line'].create({
            'move_id': move1.id,
            'product_id': move1.product_id.id,
            'qty_done': 10,
            'product_uom_id': move1.product_uom.id,
            'location_id': move1.location_id.id,
            'location_dest_id': move1.location_dest_id.id,
        })

        self.assertEqual(self.product1.value_svl, 50)
        self.assertEqual(self.product1.quantity_svl, 5)

    def test_dropship_1(self):
        self.product1.product_tmpl_id.categ_id.property_valuation = 'manual_periodic'

        move1 = self._make_dropship_move(self.product1, 10)

        valuation_layers = self.product1.stock_valuation_layer_ids
        self.assertEqual(len(valuation_layers), 2)
        self.assertEqual(valuation_layers[0].value, 100)
        self.assertEqual(valuation_layers[1].value, -100)
        self.assertEqual(self.product1.value_svl, 0)
        self.assertEqual(self.product1.quantity_svl, 0)

    def test_change_in_past_increase_dropship_1(self):
        self.product1.product_tmpl_id.categ_id.property_valuation = 'manual_periodic'

        move1 = self._make_dropship_move(self.product1, 10)
        move1.move_line_ids.qty_done = 15

        valuation_layers = self.product1.stock_valuation_layer_ids
        self.assertEqual(len(valuation_layers), 4)
        self.assertEqual(valuation_layers[0].value, 100)
        self.assertEqual(valuation_layers[1].value, -100)
        self.assertEqual(valuation_layers[2].value, 50)
        self.assertEqual(valuation_layers[3].value, -50)
        self.assertEqual(self.product1.value_svl, 0)
        self.assertEqual(self.product1.quantity_svl, 0)


class TestStockValuationAVCO(TestStockValuationCommon):
    def setUp(self):
        super(TestStockValuationAVCO, self).setUp()
        self.product1.product_tmpl_id.categ_id.property_cost_method = 'average'

    def test_normal_1(self):
        self.product1.product_tmpl_id.categ_id.property_valuation = 'manual_periodic'

        move1 = self._make_in_move(self.product1, 10, unit_cost=10)
        self.assertEqual(self.product1.standard_price, 10)
        self.assertEqual(move1.stock_valuation_layer_ids.value, 100)
        move2 = self._make_in_move(self.product1, 10, unit_cost=20)
        self.assertEqual(self.product1.standard_price, 15)
        self.assertEqual(move2.stock_valuation_layer_ids.value, 200)
        move3 = self._make_out_move(self.product1, 15)
        self.assertEqual(self.product1.standard_price, 15)
        self.assertEqual(move3.stock_valuation_layer_ids.value, -225)

        self.assertEqual(self.product1.value_svl, 75)
        self.assertEqual(self.product1.quantity_svl, 5)

    def test_change_in_past_increase_in_1(self):
        move1 = self._make_in_move(self.product1, 10, unit_cost=10)
        move2 = self._make_in_move(self.product1, 10, unit_cost=20)
        move3 = self._make_out_move(self.product1, 15)
        move1.move_line_ids.qty_done = 15

        self.assertEqual(self.product1.value_svl, 125)
        self.assertEqual(self.product1.quantity_svl, 10)

    def test_change_in_past_decrease_in_1(self):
        move1 = self._make_in_move(self.product1, 10, unit_cost=10)
        move2 = self._make_in_move(self.product1, 10, unit_cost=20)
        move3 = self._make_out_move(self.product1, 15)
        move1.move_line_ids.qty_done = 5

        self.assertEqual(self.product1.value_svl, 0)
        self.assertEqual(self.product1.quantity_svl, 0)

    def test_change_in_past_add_ml_in_1(self):
        move1 = self._make_in_move(self.product1, 10, unit_cost=10)
        move2 = self._make_in_move(self.product1, 10, unit_cost=20)
        move3 = self._make_out_move(self.product1, 15)
        self.env['stock.move.line'].create({
            'move_id': move1.id,
            'product_id': move1.product_id.id,
            'qty_done': 5,
            'product_uom_id': move1.product_uom.id,
            'location_id': move1.location_id.id,
            'location_dest_id': move1.location_dest_id.id,
        })

        self.assertEqual(self.product1.value_svl, 125)
        self.assertEqual(self.product1.quantity_svl, 10)
        self.assertEqual(self.product1.standard_price, 12.5)

    def test_change_in_past_add_move_in_1(self):
        move1 = self._make_in_move(self.product1, 10, unit_cost=10, create_picking=True)
        move2 = self._make_in_move(self.product1, 10, unit_cost=20)
        move3 = self._make_out_move(self.product1, 15)
        self.env['stock.move.line'].create({
            'product_id': move1.product_id.id,
            'qty_done': 5,
            'product_uom_id': move1.product_uom.id,
            'location_id': move1.location_id.id,
            'location_dest_id': move1.location_dest_id.id,
            'state': 'done',
            'picking_id': move1.picking_id.id,
        })

        self.assertEqual(self.product1.value_svl, 150)
        self.assertEqual(self.product1.quantity_svl, 10)
        self.assertEqual(self.product1.standard_price, 15)

    def test_change_in_past_increase_out_1(self):
        move1 = self._make_in_move(self.product1, 10, unit_cost=10)
        move2 = self._make_in_move(self.product1, 10, unit_cost=20)
        move3 = self._make_out_move(self.product1, 15)
        move3.move_line_ids.qty_done = 20

        self.assertEqual(self.product1.value_svl, 0)
        self.assertEqual(self.product1.quantity_svl, 0)
        self.assertEqual(self.product1.standard_price, 15)

    def test_change_in_past_decrease_out_1(self):
        move1 = self._make_in_move(self.product1, 10, unit_cost=10)
        move2 = self._make_in_move(self.product1, 10, unit_cost=20)
        move3 = self._make_out_move(self.product1, 15)
        move3.move_line_ids.qty_done = 10

        self.assertEqual(sum(self.product1.stock_valuation_layer_ids.mapped('remaining_qty')), 10)
        self.assertEqual(self.product1.value_svl, 150)
        self.assertEqual(self.product1.quantity_svl, 10)
        self.assertEqual(self.product1.standard_price, 15)

    def test_negative_1(self):
        """ Ensures that, in AVCO, the `remaining_qty` field is computed and the vacuum is ran
        when necessary.
        """
        self.product1.product_tmpl_id.categ_id.property_valuation = 'manual_periodic'
        move1 = self._make_in_move(self.product1, 10, unit_cost=10)
        move2 = self._make_in_move(self.product1, 10, unit_cost=20)
        move3 = self._make_out_move(self.product1, 30)
        self.assertEqual(move3.stock_valuation_layer_ids.remaining_qty, -10)
        move4 = self._make_in_move(self.product1, 10, unit_cost=30)
        self.assertEqual(sum(self.product1.stock_valuation_layer_ids.mapped('remaining_qty')), 0)
        move5 = self._make_in_move(self.product1, 10, unit_cost=40)

        self.assertEqual(self.product1.value_svl, 400)
        self.assertEqual(self.product1.quantity_svl, 10)

    def test_negative_2(self):
        self.product1.product_tmpl_id.categ_id.property_valuation = 'manual_periodic'
        self.product1.standard_price = 10
        move1 = self._make_out_move(self.product1, 1, force_assign=True)
        move2 = self._make_in_move(self.product1, 1, unit_cost=15)

        self.assertEqual(self.product1.value_svl, 0)
        self.assertEqual(self.product1.quantity_svl, 0)

    def test_return_receipt_1(self):
        move1 = self._make_in_move(self.product1, 1, unit_cost=10, create_picking=True)
        move2 = self._make_in_move(self.product1, 1, unit_cost=20)
        move3 = self._make_out_move(self.product1, 1)
        move4 = self._make_return(move1, 1)

        self.assertEqual(self.product1.value_svl, 0)
        self.assertEqual(self.product1.quantity_svl, 0)
        self.assertEqual(self.product1.standard_price, 15)

    def test_return_delivery_1(self):
        move1 = self._make_in_move(self.product1, 1, unit_cost=10)
        move2 = self._make_in_move(self.product1, 1, unit_cost=20)
        move3 = self._make_out_move(self.product1, 1, create_picking=True)
        move4 = self._make_return(move3, 1)

        self.assertEqual(self.product1.value_svl, 30)
        self.assertEqual(self.product1.quantity_svl, 2)
        self.assertEqual(self.product1.standard_price, 15)
        self.assertEqual(sum(self.product1.stock_valuation_layer_ids.mapped('remaining_qty')), 2)

    def test_rereturn_receipt_1(self):
        move1 = self._make_in_move(self.product1, 1, unit_cost=10, create_picking=True)
        move2 = self._make_in_move(self.product1, 1, unit_cost=20)
        move3 = self._make_out_move(self.product1, 1)
        move4 = self._make_return(move1, 1)  # -15, current avco
        move5 = self._make_return(move4, 1)  # +10, original move's price unit

        self.assertEqual(self.product1.value_svl, 15)
        self.assertEqual(self.product1.quantity_svl, 1)
        self.assertEqual(self.product1.standard_price, 15)
        self.assertEqual(sum(self.product1.stock_valuation_layer_ids.mapped('remaining_qty')), 1)

    def test_rereturn_delivery_1(self):
        move1 = self._make_in_move(self.product1, 1, unit_cost=10)
        move2 = self._make_in_move(self.product1, 1, unit_cost=20)
        move3 = self._make_out_move(self.product1, 1, create_picking=True)
        move4 = self._make_return(move3, 1)
        move5 = self._make_return(move4, 1)

        self.assertEqual(self.product1.value_svl, 15)
        self.assertEqual(self.product1.quantity_svl, 1)
        self.assertEqual(self.product1.standard_price, 15)
        self.assertEqual(sum(self.product1.stock_valuation_layer_ids.mapped('remaining_qty')), 1)

    def test_dropship_1(self):
        move1 = self._make_in_move(self.product1, 1, unit_cost=10)
        move2 = self._make_in_move(self.product1, 1, unit_cost=20)
        move3 = self._make_dropship_move(self.product1, 1, unit_cost=10)

        self.assertEqual(self.product1.value_svl, 30)
        self.assertEqual(self.product1.quantity_svl, 2)
        self.assertEqual(self.product1.standard_price, 15)


class TestStockValuationFIFO(TestStockValuationCommon):
    def setUp(self):
        super(TestStockValuationFIFO, self).setUp()
        self.product1.product_tmpl_id.categ_id.property_cost_method = 'fifo'

    def test_normal_1(self):
        self.product1.product_tmpl_id.categ_id.property_valuation = 'manual_periodic'
        move1 = self._make_in_move(self.product1, 10, unit_cost=10)
        move2 = self._make_in_move(self.product1, 10, unit_cost=20)
        move3 = self._make_out_move(self.product1, 15)

        self.assertEqual(self.product1.value_svl, 100)
        self.assertEqual(self.product1.quantity_svl, 5)
        self.assertEqual(sum(self.product1.stock_valuation_layer_ids.mapped('remaining_qty')), 5)

    def test_negative_1(self):
        self.product1.product_tmpl_id.categ_id.property_valuation = 'manual_periodic'
        move1 = self._make_in_move(self.product1, 10, unit_cost=10)
        move2 = self._make_in_move(self.product1, 10, unit_cost=20)
        move3 = self._make_out_move(self.product1, 30)
        self.assertEqual(move3.stock_valuation_layer_ids.remaining_qty, -10)
        move4 = self._make_in_move(self.product1, 10, unit_cost=30)
        self.assertEqual(sum(self.product1.stock_valuation_layer_ids.mapped('remaining_qty')), 0)
        move5 = self._make_in_move(self.product1, 10, unit_cost=40)

        self.assertEqual(self.product1.value_svl, 400)
        self.assertEqual(self.product1.quantity_svl, 10)

    def test_change_in_past_decrease_in_1(self):
        self.product1.product_tmpl_id.categ_id.property_valuation = 'manual_periodic'
        move1 = self._make_in_move(self.product1, 20, unit_cost=10)
        move2 = self._make_out_move(self.product1, 10)
        move1.move_line_ids.qty_done = 10

        self.assertEqual(self.product1.value_svl, 0)
        self.assertEqual(self.product1.quantity_svl, 0)

    def test_change_in_past_decrease_in_2(self):
        self.product1.product_tmpl_id.categ_id.property_valuation = 'manual_periodic'
        move1 = self._make_in_move(self.product1, 20, unit_cost=10)
        move2 = self._make_out_move(self.product1, 10)
        move3 = self._make_out_move(self.product1, 10)
        move1.move_line_ids.qty_done = 10
        move4 = self._make_in_move(self.product1, 20, unit_cost=15)

        self.assertEqual(self.product1.value_svl, 150)
        self.assertEqual(self.product1.quantity_svl, 10)

    def test_change_in_past_increase_in_1(self):
        self.product1.product_tmpl_id.categ_id.property_valuation = 'manual_periodic'
        move1 = self._make_in_move(self.product1, 10, unit_cost=10)
        move2 = self._make_in_move(self.product1, 10, unit_cost=15)
        move3 = self._make_out_move(self.product1, 20)
        move1.move_line_ids.qty_done = 20

        self.assertEqual(self.product1.value_svl, 100)
        self.assertEqual(self.product1.quantity_svl, 10)

    def test_change_in_past_increase_in_2(self):
        self.product1.product_tmpl_id.categ_id.property_valuation = 'manual_periodic'
        move1 = self._make_in_move(self.product1, 10, unit_cost=10)
        move2 = self._make_in_move(self.product1, 10, unit_cost=12)
        move3 = self._make_out_move(self.product1, 15)
        move4 = self._make_out_move(self.product1, 20)
        move5 = self._make_in_move(self.product1, 100, unit_cost=15)
        move1.move_line_ids.qty_done = 20

        self.assertEqual(self.product1.value_svl, 1375)
        self.assertEqual(self.product1.quantity_svl, 95)

    def test_change_in_past_increase_out_1(self):
        self.product1.product_tmpl_id.categ_id.property_valuation = 'manual_periodic'
        move1 = self._make_in_move(self.product1, 20, unit_cost=10)
        move2 = self._make_out_move(self.product1, 10)
        move3 = self._make_in_move(self.product1, 20, unit_cost=15)
        move2.move_line_ids.qty_done = 25

        self.assertEqual(self.product1.value_svl, 225)
        self.assertEqual(self.product1.quantity_svl, 15)
        self.assertEqual(sum(self.product1.stock_valuation_layer_ids.mapped('remaining_qty')), 15)

    def test_change_in_past_decrease_out_1(self):
        self.product1.product_tmpl_id.categ_id.property_valuation = 'manual_periodic'
        move1 = self._make_in_move(self.product1, 20, unit_cost=10)
        move2 = self._make_out_move(self.product1, 15)
        move3 = self._make_in_move(self.product1, 20, unit_cost=15)
        move2.move_line_ids.qty_done = 5

        self.assertEqual(self.product1.value_svl, 450)
        self.assertEqual(self.product1.quantity_svl, 35)
        self.assertEqual(sum(self.product1.stock_valuation_layer_ids.mapped('remaining_qty')), 35)

    def test_change_in_past_add_ml_out_1(self):
        self.product1.product_tmpl_id.categ_id.property_valuation = 'manual_periodic'
        move1 = self._make_in_move(self.product1, 20, unit_cost=10)
        move2 = self._make_out_move(self.product1, 10)
        move3 = self._make_in_move(self.product1, 20, unit_cost=15)
        self.env['stock.move.line'].create({
            'move_id': move2.id,
            'product_id': move2.product_id.id,
            'qty_done': 5,
            'product_uom_id': move2.product_uom.id,
            'location_id': move2.location_id.id,
            'location_dest_id': move2.location_dest_id.id,
        })

        self.assertEqual(self.product1.value_svl, 350)
        self.assertEqual(self.product1.quantity_svl, 25)
        self.assertEqual(sum(self.product1.stock_valuation_layer_ids.mapped('remaining_qty')), 25)

    def test_return_delivery_1(self):
        self.product1.product_tmpl_id.categ_id.property_valuation = 'manual_periodic'
        move1 = self._make_in_move(self.product1, 10, unit_cost=10)
        move2 = self._make_out_move(self.product1, 10, create_picking=True)
        move3 = self._make_in_move(self.product1, 10, unit_cost=20)
        move4 = self._make_return(move2, 10)

        self.assertEqual(self.product1.value_svl, 300)
        self.assertEqual(self.product1.quantity_svl, 20)

    def test_return_receipt_1(self):
        self.product1.product_tmpl_id.categ_id.property_valuation = 'manual_periodic'
        move1 = self._make_in_move(self.product1, 10, unit_cost=10, create_picking=True)
        move2 = self._make_in_move(self.product1, 10, unit_cost=20)
        move3 = self._make_return(move1, 2)

        self.assertEqual(self.product1.value_svl, 280)
        self.assertEqual(self.product1.quantity_svl, 18)

    def test_rereturn_receipt_1(self):
        move1 = self._make_in_move(self.product1, 1, unit_cost=10, create_picking=True)
        move2 = self._make_in_move(self.product1, 1, unit_cost=20)
        move3 = self._make_out_move(self.product1, 1)
        move4 = self._make_return(move1, 1)
        move5 = self._make_return(move4, 1)

        self.assertEqual(self.product1.value_svl, 20)
        self.assertEqual(self.product1.quantity_svl, 1)

    def test_rereturn_delivery_1(self):
        move1 = self._make_in_move(self.product1, 1, unit_cost=10)
        move2 = self._make_in_move(self.product1, 1, unit_cost=20)
        move3 = self._make_out_move(self.product1, 1, create_picking=True)
        move4 = self._make_return(move3, 1)
        move5 = self._make_return(move4, 1)

        self.assertEqual(self.product1.value_svl, 10)
        self.assertEqual(self.product1.quantity_svl, 1)

    def test_dropship_1(self):
        orig_standard_price = self.product1.standard_price
        move1 = self._make_in_move(self.product1, 1, unit_cost=10)
        move2 = self._make_in_move(self.product1, 1, unit_cost=20)
        move3 = self._make_dropship_move(self.product1, 1, unit_cost=10)

        self.assertEqual(self.product1.value_svl, 30)
        self.assertEqual(self.product1.quantity_svl, 2)
        self.assertEqual(orig_standard_price, self.product1.standard_price)


class TestStockValuationChangeCostMethod(TestStockValuationCommon):
    def test_standard_to_fifo_1(self):
        """ The accounting impact of this cost method change is neutral.
        """
        self.product1.product_tmpl_id.categ_id.property_cost_method = 'standard'
        self.product1.product_tmpl_id.categ_id.property_valuation = 'manual_periodic'
        self.product1.product_tmpl_id.standard_price = 10

        move1 = self._make_in_move(self.product1, 10)
        move2 = self._make_in_move(self.product1, 10)
        move3 = self._make_out_move(self.product1, 1)

        self.product1.product_tmpl_id.categ_id.property_cost_method = 'fifo'
        self.assertEqual(self.product1.value_svl, 190)
        self.assertEqual(self.product1.quantity_svl, 19)

        self.assertEqual(len(self.product1.stock_valuation_layer_ids), 5)
        for svl in self.product1.stock_valuation_layer_ids[-2:]:
            self.assertEqual(svl.description, 'Costing method change for product category All: from standard to fifo.')

    def test_standard_to_fifo_2(self):
        """ We want the same result as `test_standard_to_fifo_1` but by changing the category of
        `self.product1` to another one, not changing the current one.
        """
        self.product1.product_tmpl_id.categ_id.property_cost_method = 'standard'
        self.product1.product_tmpl_id.categ_id.property_valuation = 'manual_periodic'
        self.product1.product_tmpl_id.standard_price = 10

        move1 = self._make_in_move(self.product1, 10)
        move2 = self._make_in_move(self.product1, 10)
        move3 = self._make_out_move(self.product1, 1)

        cat2 = self.env['product.category'].create({'name': 'fifo'})
        cat2.property_cost_method = 'fifo'
        self.product1.product_tmpl_id.categ_id = cat2
        self.assertEqual(self.product1.value_svl, 190)
        self.assertEqual(self.product1.quantity_svl, 19)
        self.assertEqual(len(self.product1.stock_valuation_layer_ids), 5)

    def test_avco_to_fifo(self):
        """ The accounting impact of this cost method change is neutral.
        """
        self.product1.product_tmpl_id.categ_id.property_cost_method = 'average'
        self.product1.product_tmpl_id.categ_id.property_valuation = 'manual_periodic'

        move1 = self._make_in_move(self.product1, 10, unit_cost=10)
        move2 = self._make_in_move(self.product1, 10, unit_cost=20)
        move3 = self._make_out_move(self.product1, 1)

        self.product1.product_tmpl_id.categ_id.property_cost_method = 'fifo'
        self.assertEqual(self.product1.value_svl, 285)
        self.assertEqual(self.product1.quantity_svl, 19)

    def test_fifo_to_standard(self):
        """ The accounting impact of this cost method change is not neutral as we will use the last
        fifo price as the new standard price.
        """
        self.product1.product_tmpl_id.categ_id.property_cost_method = 'fifo'
        self.product1.product_tmpl_id.categ_id.property_valuation = 'manual_periodic'

        move1 = self._make_in_move(self.product1, 10, unit_cost=10)
        move2 = self._make_in_move(self.product1, 10, unit_cost=20)
        move3 = self._make_out_move(self.product1, 1)

        self.product1.product_tmpl_id.categ_id.property_cost_method = 'standard'
        self.assertEqual(self.product1.value_svl, 380)
        self.assertEqual(self.product1.quantity_svl, 19)

    def test_fifo_to_avco(self):
        """ The accounting impact of this cost method change is not neutral as we will use the last
        fifo price as the new AVCO.
        """
        self.product1.product_tmpl_id.categ_id.property_cost_method = 'fifo'
        self.product1.product_tmpl_id.categ_id.property_valuation = 'manual_periodic'

        move1 = self._make_in_move(self.product1, 10, unit_cost=10)
        move2 = self._make_in_move(self.product1, 10, unit_cost=20)
        move3 = self._make_out_move(self.product1, 1)

        self.product1.product_tmpl_id.categ_id.property_cost_method = 'average'
        self.assertEqual(self.product1.value_svl, 380)
        self.assertEqual(self.product1.quantity_svl, 19)

    def test_avco_to_standard(self):
        """ The accounting impact of this cost method change is neutral.
        """
        self.product1.product_tmpl_id.categ_id.property_cost_method = 'average'
        self.product1.product_tmpl_id.categ_id.property_valuation = 'manual_periodic'

        move1 = self._make_in_move(self.product1, 10, unit_cost=10)
        move2 = self._make_in_move(self.product1, 10, unit_cost=20)
        move3 = self._make_out_move(self.product1, 1)

        self.product1.product_tmpl_id.categ_id.property_cost_method = 'standard'
        self.assertEqual(self.product1.value_svl, 285)
        self.assertEqual(self.product1.quantity_svl, 19)

    def test_standard_to_avco(self):
        """ The accounting impact of this cost method change is neutral.
        """
        self.product1.product_tmpl_id.categ_id.property_cost_method = 'standard'
        self.product1.product_tmpl_id.categ_id.property_valuation = 'manual_periodic'
        self.product1.product_tmpl_id.standard_price = 10

        move1 = self._make_in_move(self.product1, 10)
        move2 = self._make_in_move(self.product1, 10)
        move3 = self._make_out_move(self.product1, 1)

        self.product1.product_tmpl_id.categ_id.property_cost_method = 'average'
        self.assertEqual(self.product1.value_svl, 190)
        self.assertEqual(self.product1.quantity_svl, 19)


@tagged('post_install', '-at_install')
class TestStockValuationChangeValuation(TestStockValuationCommon):
    def test_standard_manual_to_auto_1(self):
        self.product1.product_tmpl_id.categ_id.property_cost_method = 'standard'
        self.product1.product_tmpl_id.categ_id.property_valuation = 'manual_periodic'
        self.product1.product_tmpl_id.standard_price = 10
        move1 = self._make_in_move(self.product1, 10)

        self.assertEqual(self.product1.value_svl, 100)
        self.assertEqual(self.product1.quantity_svl, 10)
        self.assertEqual(len(self.product1.stock_valuation_layer_ids.mapped('account_move_id')), 0)
        self.assertEqual(len(self.product1.stock_valuation_layer_ids), 1)

        self.product1.product_tmpl_id.categ_id.property_valuation = 'real_time'

        self.assertEqual(self.product1.value_svl, 100)
        self.assertEqual(self.product1.quantity_svl, 10)
        # An accounting entry should only be created for the replenish now that the category is perpetual.
        self.assertEqual(len(self.product1.stock_valuation_layer_ids.mapped('account_move_id')), 1)
        self.assertEqual(len(self.product1.stock_valuation_layer_ids), 3)
        for svl in self.product1.stock_valuation_layer_ids[-2:]:
            self.assertEqual(svl.description, 'Valuation method change for product category All: from manual_periodic to real_time.')

    def test_standard_manual_to_auto_2(self):
        self.product1.product_tmpl_id.categ_id.property_cost_method = 'standard'
        self.product1.product_tmpl_id.categ_id.property_valuation = 'manual_periodic'
        self.product1.product_tmpl_id.standard_price = 10
        move1 = self._make_in_move(self.product1, 10)

        self.assertEqual(self.product1.value_svl, 100)
        self.assertEqual(self.product1.quantity_svl, 10)
        self.assertEqual(len(self.product1.stock_valuation_layer_ids.mapped('account_move_id')), 0)
        self.assertEqual(len(self.product1.stock_valuation_layer_ids), 1)

        cat2 = self.env['product.category'].create({'name': 'fifo'})
        cat2.property_cost_method = 'standard'
        cat2.property_valuation = 'real_time'
        self.product1.categ_id = cat2

        self.assertEqual(self.product1.value_svl, 100)
        self.assertEqual(self.product1.quantity_svl, 10)
        # An accounting entry should only be created for the replenish now that the category is perpetual.
        self.assertEqual(len(self.product1.stock_valuation_layer_ids.mapped('account_move_id')), 1)
        self.assertEqual(len(self.product1.stock_valuation_layer_ids), 3)

    def test_standard_auto_to_manual_1(self):
        self.product1.product_tmpl_id.categ_id.property_cost_method = 'standard'
        self.product1.product_tmpl_id.categ_id.property_valuation = 'real_time'
        self.product1.product_tmpl_id.standard_price = 10
        move1 = self._make_in_move(self.product1, 10)

        self.assertEqual(self.product1.value_svl, 100)
        self.assertEqual(self.product1.quantity_svl, 10)
        self.assertEqual(len(self.product1.stock_valuation_layer_ids.mapped('account_move_id')), 1)
        self.assertEqual(len(self.product1.stock_valuation_layer_ids), 1)

        self.product1.product_tmpl_id.categ_id.property_valuation = 'manual_periodic'

        self.assertEqual(self.product1.value_svl, 100)
        self.assertEqual(self.product1.quantity_svl, 10)
        # An accounting entry should only be created for the emptying now that the category is manual.
        self.assertEqual(len(self.product1.stock_valuation_layer_ids.mapped('account_move_id')), 2)
        self.assertEqual(len(self.product1.stock_valuation_layer_ids), 3)

    def test_standard_auto_to_manual_2(self):
        self.product1.product_tmpl_id.categ_id.property_cost_method = 'standard'
        self.product1.product_tmpl_id.categ_id.property_valuation = 'real_time'
        self.product1.product_tmpl_id.standard_price = 10
        move1 = self._make_in_move(self.product1, 10)

        self.assertEqual(self.product1.value_svl, 100)
        self.assertEqual(self.product1.quantity_svl, 10)
        self.assertEqual(len(self.product1.stock_valuation_layer_ids.mapped('account_move_id')), 1)
        self.assertEqual(len(self.product1.stock_valuation_layer_ids), 1)

        cat2 = self.env['product.category'].create({'name': 'fifo'})
        cat2.property_cost_method = 'standard'
        cat2.property_valuation = 'manual_periodic'
        self.product1.with_context(debug=True).categ_id = cat2

        self.assertEqual(self.product1.value_svl, 100)
        self.assertEqual(self.product1.quantity_svl, 10)
        # An accounting entry should only be created for the emptying now that the category is manual.
        self.assertEqual(len(self.product1.stock_valuation_layer_ids.mapped('account_move_id')), 2)
        self.assertEqual(len(self.product1.stock_valuation_layer_ids), 3)

