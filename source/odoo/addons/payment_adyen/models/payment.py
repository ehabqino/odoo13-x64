# coding: utf-8

import base64
import json
import binascii
from collections import OrderedDict
import hashlib
import hmac
import logging
from itertools import chain

from werkzeug import urls

from odoo import api, fields, models, tools, _
from odoo.addons.payment.models.payment_acquirer import ValidationError
from odoo.addons.payment_adyen.controllers.main import AdyenController
from odoo.tools.pycompat import to_text

_logger = logging.getLogger(__name__)

# https://docs.adyen.com/developers/development-resources/currency-codes
CURRENCY_CODE_MAPS = {
    "BHD": 3,
    "CVE": 0,
    "DJF": 0,
    "GNF": 0,
    "IDR": 0,
    "JOD": 3,
    "JPY": 0,
    "KMF": 0,
    "KRW": 0,
    "KWD": 3,
    "LYD": 3,
    "OMR": 3,
    "PYG": 0,
    "RWF": 0,
    "TND": 3,
    "UGX": 0,
    "VND": 0,
    "VUV": 0,
    "XAF": 0,
    "XOF": 0,
    "XPF": 0,
}


class AcquirerAdyen(models.Model):
    _inherit = 'payment.acquirer'

    provider = fields.Selection(selection_add=[('adyen', 'Adyen')])
    adyen_merchant_account = fields.Char('Merchant Account', required_if_provider='adyen', groups='base.group_user')
    adyen_skin_code = fields.Char('Skin Code', required_if_provider='adyen', groups='base.group_user')
    adyen_skin_hmac_key = fields.Char('Skin HMAC Key', required_if_provider='adyen', groups='base.group_user')

    @api.model
    def _adyen_convert_amount(self, amount, currency):
        """
        Adyen requires the amount to be multiplied by 10^k,
        where k depends on the currency code.
        """
        k = CURRENCY_CODE_MAPS.get(currency.name, 2)
        paymentAmount = int(tools.float_round(amount, k) * (10**k))
        return paymentAmount

    @api.model
    def _get_adyen_urls(self, environment):
        """ Adyen URLs: yhpp: hosted payment page: pay.shtml for single, select.shtml for multiple """
        return {
            'adyen_form_url': 'https://%s.adyen.com/hpp/pay.shtml' % ('live' if environment == 'prod' else environment),
        }

    def _adyen_generate_merchant_sig_sha256(self, inout, values):
        """ Generate the shasign for incoming or outgoing communications., when using the SHA-256
        signature.

        :param string inout: 'in' (odoo contacting ogone) or 'out' (adyen
                             contacting odoo). In this last case only some
                             fields should be contained (see e-Commerce basic)
        :param dict values: transaction values
        :return string: shasign
        """
        def escapeVal(val):
            return val.replace('\\', '\\\\').replace(':', '\\:')

        def signParams(parms):
            signing_string = ':'.join(
                escapeVal(v)
                for v in chain(parms.keys(), parms.values())
            )
            hm = hmac.new(hmac_key, signing_string.encode('utf-8'), hashlib.sha256)
            return base64.b64encode(hm.digest())

        assert inout in ('in', 'out')
        assert self.provider == 'adyen'

        if inout == 'in':
            # All the fields sent to Adyen must be included in the signature. ALL the fucking
            # fields, despite what is claimed in the documentation. For example, in
            # https://docs.adyen.com/developers/hpp-manual, it is stated: "The resURL parameter does
            # not need to be included in the signature." It's a trap, it must be included as well!
            keys = [
                'merchantReference', 'paymentAmount', 'currencyCode', 'shipBeforeDate', 'skinCode',
                'merchantAccount', 'sessionValidity', 'merchantReturnData', 'shopperEmail',
                'shopperReference', 'allowedMethods', 'blockedMethods', 'offset',
                'shopperStatement', 'recurringContract', 'billingAddressType',
                'deliveryAddressType', 'brandCode', 'countryCode', 'shopperLocale', 'orderData',
                'offerEmail', 'resURL',
            ]
        else:
            keys = [
                'authResult', 'merchantReference', 'merchantReturnData', 'paymentMethod',
                'pspReference', 'shopperLocale', 'skinCode',
            ]

        hmac_key = binascii.a2b_hex(self.adyen_skin_hmac_key.encode('ascii'))
        raw_values = {k: values.get(k, '') for k in keys if k in values}
        raw_values_ordered = OrderedDict(sorted(raw_values.items(), key=lambda t: t[0]))

        return signParams(raw_values_ordered)

    def _adyen_generate_merchant_sig(self, inout, values):
        """ Generate the shasign for incoming or outgoing communications, when using the SHA-1
        signature (deprecated by Adyen).

        :param string inout: 'in' (odoo contacting ogone) or 'out' (adyen
                             contacting odoo). In this last case only some
                             fields should be contained (see e-Commerce basic)
        :param dict values: transaction values

        :return string: shasign
        """
        assert inout in ('in', 'out')
        assert self.provider == 'adyen'

        if inout == 'in':
            keys = "paymentAmount currencyCode shipBeforeDate merchantReference skinCode merchantAccount sessionValidity shopperEmail shopperReference recurringContract allowedMethods blockedMethods shopperStatement merchantReturnData billingAddressType deliveryAddressType offset".split()
        else:
            keys = "authResult pspReference merchantReference skinCode merchantReturnData".split()

        def get_value(key):
            if values.get(key):
                return values[key]
            return ''

        sign = ''.join('%s' % get_value(k) for k in keys).encode('ascii')
        key = self.adyen_skin_hmac_key.encode('ascii')
        return base64.b64encode(hmac.new(key, sign, hashlib.sha1).digest())

    def adyen_form_generate_values(self, values):
        base_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url')
        # tmp
        import datetime
        from dateutil import relativedelta

        paymentAmount = self._adyen_convert_amount(values['amount'], values['currency'])
        if self.provider == 'adyen' and len(self.adyen_skin_hmac_key) == 64:
            tmp_date = datetime.datetime.today() + relativedelta.relativedelta(days=1)

            values.update({
                'merchantReference': values['reference'],
                'paymentAmount': '%d' % paymentAmount,
                'currencyCode': values['currency'] and values['currency'].name or '',
                'shipBeforeDate': tmp_date.strftime('%Y-%m-%d'),
                'skinCode': self.adyen_skin_code,
                'merchantAccount': self.adyen_merchant_account,
                'shopperLocale': values.get('partner_lang', ''),
                'sessionValidity': tmp_date.isoformat('T')[:19] + "Z",
                'resURL': urls.url_join(base_url, AdyenController._return_url),
                'merchantReturnData': json.dumps({'return_url': '%s' % values.pop('return_url')}) if values.get('return_url', '') else False,
                'shopperEmail': values.get('partner_email') or values.get('billing_partner_email') or '',
            })
            values['merchantSig'] = self._adyen_generate_merchant_sig_sha256('in', values)

        else:
            tmp_date = datetime.date.today() + relativedelta.relativedelta(days=1)

            values.update({
                'merchantReference': values['reference'],
                'paymentAmount': '%d' % paymentAmount,
                'currencyCode': values['currency'] and values['currency'].name or '',
                'shipBeforeDate': tmp_date,
                'skinCode': self.adyen_skin_code,
                'merchantAccount': self.adyen_merchant_account,
                'shopperLocale': values.get('partner_lang'),
                'sessionValidity': tmp_date,
                'resURL': urls.url_join(base_url, AdyenController._return_url),
                'merchantReturnData': json.dumps({'return_url': '%s' % values.pop('return_url')}) if values.get('return_url') else False,
            })
            values['merchantSig'] = self._adyen_generate_merchant_sig('in', values)

        return values

    def adyen_get_form_action_url(self):
        return self._get_adyen_urls(self.environment)['adyen_form_url']


class TxAdyen(models.Model):
    _inherit = 'payment.transaction'

    # --------------------------------------------------
    # FORM RELATED METHODS
    # --------------------------------------------------

    @api.model
    def _adyen_form_get_tx_from_data(self, data):
        reference, pspReference = data.get('merchantReference'), data.get('pspReference')
        if not reference or not pspReference:
            error_msg = _('Adyen: received data with missing reference (%s) or missing pspReference (%s)') % (reference, pspReference)
            _logger.info(error_msg)
            raise ValidationError(error_msg)

        # find tx -> @TDENOTE use pspReference ?
        tx = self.env['payment.transaction'].search([('reference', '=', reference)])
        if not tx or len(tx) > 1:
            error_msg = _('Adyen: received data for reference %s') % (reference)
            if not tx:
                error_msg += _('; no order found')
            else:
                error_msg += _('; multiple order found')
            _logger.info(error_msg)
            raise ValidationError(error_msg)

        # verify shasign
        if len(tx.acquirer_id.adyen_skin_hmac_key) == 64:
            shasign_check = tx.acquirer_id._adyen_generate_merchant_sig_sha256('out', data)
        else:
            shasign_check = tx.acquirer_id._adyen_generate_merchant_sig('out', data)
        if to_text(shasign_check) != to_text(data.get('merchantSig')):
            error_msg = _('Adyen: invalid merchantSig, received %s, computed %s') % (data.get('merchantSig'), shasign_check)
            _logger.warning(error_msg)
            raise ValidationError(error_msg)

        return tx

    def _adyen_form_get_invalid_parameters(self, data):
        invalid_parameters = []

        # reference at acquirer: pspReference
        if self.acquirer_reference and data.get('pspReference') != self.acquirer_reference:
            invalid_parameters.append(('pspReference', data.get('pspReference'), self.acquirer_reference))
        # seller
        if data.get('skinCode') != self.acquirer_id.adyen_skin_code:
            invalid_parameters.append(('skinCode', data.get('skinCode'), self.acquirer_id.adyen_skin_code))
        # result
        if not data.get('authResult'):
            invalid_parameters.append(('authResult', data.get('authResult'), 'something'))

        return invalid_parameters

    def _adyen_form_validate(self, data):
        status = data.get('authResult', 'PENDING')
        if status == 'AUTHORISED':
            self.write({'acquirer_reference': data.get('pspReference')})
            self._set_transaction_done()
            return True
        elif status == 'PENDING':
            self.write({'acquirer_reference': data.get('pspReference')})
            self._set_transaction_pending()
            return True
        else:
            error = _('Adyen: feedback error')
            _logger.info(error)
            self.write({'state_message': error})
            self._set_transaction_cancel()
            return False
