# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.

import tempfile

from odoo import api, fields, models, tools, _
from odoo.exceptions import UserError


class BaseUpdateTranslations(models.TransientModel):
    _name = 'base.update.translations'
    _description = 'Update Translations'

    @api.model
    def _get_languages(self):
        langs = self.env['res.lang'].search([('active', '=', True), ('translatable', '=', True)])
        return [(lang.code, lang.name) for lang in langs]


    lang = fields.Selection(_get_languages, 'Language', required=True)

    @api.model
    def _get_lang_name(self, lang_code):
        lang = self.env['res.lang'].search([('code', '=', lang_code)], limit=1)
        if not lang:
            raise UserError(_('No language with code "%s" exists') % lang_code)
        return lang.name

    def act_update(self):
        this = self[0]
        lang_name = self._get_lang_name(this.lang)
        with tempfile.NamedTemporaryFile() as buf:
            tools.trans_export(this.lang, ['all'], buf, 'po', self._cr)
            context = {'create_empty_translation': True}
            tools.trans_load_data(self._cr, buf, 'po', this.lang, lang_name=lang_name, context=context)
        return {'type': 'ir.actions.act_window_close'}
