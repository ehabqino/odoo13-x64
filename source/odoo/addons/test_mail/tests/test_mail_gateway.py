# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.

import socket

from email.utils import formataddr

from odoo.addons.test_mail.data import test_mail_data
from odoo.addons.test_mail.data.test_mail_data import MAIL_TEMPLATE
from odoo.addons.test_mail.tests.common import BaseFunctionalTest, MockEmails
from odoo.addons.test_mail.tests.common import mail_new_test_user
from odoo.tools import mute_logger


from odoo.tests import tagged


@tagged('mail_gateway')
class TestEmailParsing(BaseFunctionalTest, MockEmails):

    def test_message_parse_body(self):
        # test pure plaintext
        plaintext = self.format(test_mail_data.MAIL_TEMPLATE_PLAINTEXT, email_from='Sylvie Lelitre <test.sylvie.lelitre@agrolait.com>')
        res = self.env['mail.thread'].message_parse(plaintext)
        self.assertIn('Please call me as soon as possible this afternoon!', res['body'])

        # test multipart / text and html -> html has priority
        multipart = self.format(MAIL_TEMPLATE, email_from='Sylvie Lelitre <test.sylvie.lelitre@agrolait.com>')
        res = self.env['mail.thread'].message_parse(multipart)
        self.assertIn('<p>Please call me as soon as possible this afternoon!</p>', res['body'])

        # test multipart / mixed
        res = self.env['mail.thread'].message_parse(test_mail_data.MAIL_MULTIPART_MIXED)
        self.assertNotIn(
            'Should create a multipart/mixed: from gmail, *bold*, with attachment', res['body'],
            'message_parse: text version should not be in body after parsing multipart/mixed')
        self.assertIn(
            '<div dir="ltr">Should create a multipart/mixed: from gmail, <b>bold</b>, with attachment.<br clear="all"><div><br></div>', res['body'],
            'message_parse: html version should be in body after parsing multipart/mixed')

        res = self.env['mail.thread'].message_parse(test_mail_data.MAIL_MULTIPART_MIXED_TWO)
        self.assertNotIn('First and second part', res['body'],
                         'message_parse: text version should not be in body after parsing multipart/mixed')
        self.assertIn('First part', res['body'],
                      'message_parse: first part of the html version should be in body after parsing multipart/mixed')
        self.assertIn('Second part', res['body'],
                      'message_parse: second part of the html version should be in body after parsing multipart/mixed')

        res = self.env['mail.thread'].message_parse(test_mail_data.MAIL_SINGLE_BINARY)
        self.assertEqual(res['body'], '')
        self.assertEqual(res['attachments'][0][0], 'thetruth.pdf')

    def test_message_parse_eml(self):
        # Test that the parsing of mail with embedded emails as eml(msg) which generates empty attachments, can be processed.
        self.env['mail.thread'].message_parse(self.format(test_mail_data.MAIL_EML_ATTACHMENT, email_from='Sylvie Lelitre <test.sylvie.lelitre@agrolait.com>', to='generic@test.com'))

    def test_message_parse_plaintext(self):
        """ Incoming email in plaintext should be stored as html """
        res = self.env['mail.thread'].message_parse(self.format(test_mail_data.MAIL_TEMPLATE_PLAINTEXT, email_from='Sylvie Lelitre <test.sylvie.lelitre@agrolait.com>', to='generic@test.com'))
        self.assertIn('<pre>\nPlease call me as soon as possible this afternoon!\n\n--\nSylvie\n</pre>', res['body'])

    def test_message_parse_xhtml(self):
        # Test that the parsing of XHTML mails does not fail
        self.env['mail.thread'].message_parse(test_mail_data.MAIL_XHTML)


@tagged('mail_gateway')
class TestMailgateway(BaseFunctionalTest, MockEmails):

    @classmethod
    def setUpClass(cls):
        super(TestMailgateway, cls).setUpClass()
        cls.test_model = cls.env['ir.model']._get('mail.test.gateway')
        cls.email_from = 'Sylvie Lelitre <test.sylvie.lelitre@agrolait.com>'

        cls.test_record = cls.env['mail.test.gateway'].with_context(cls._test_context).create({
            'name': 'Test',
            'email_from': 'ignasse@example.com',
        }).with_context({})

        cls.partner_1 = cls.env['res.partner'].with_context(cls._test_context).create({
            'name': 'Valid Lelitre',
            'email': 'valid.lelitre@agrolait.com',
        })
        # groups@.. will cause the creation of new mail.test.gateway
        cls.alias = cls.env['mail.alias'].create({
            'alias_name': 'groups',
            'alias_user_id': False,
            'alias_model_id': cls.test_model.id,
            'alias_contact': 'everyone'})

        # Set a first message on public group to test update and hierarchy
        cls.fake_email = cls.env['mail.message'].create({
            'model': 'mail.test.gateway',
            'res_id': cls.test_record.id,
            'subject': 'Public Discussion',
            'message_type': 'email',
            'subtype_id': cls.env.ref('mail.mt_comment').id,
            'author_id': cls.partner_1.id,
            'message_id': '<123456-openerp-%s-mail.test.gateway@%s>' % (cls.test_record.id, socket.gethostname()),
        })

        cls.env['ir.config_parameter'].set_param('mail.bounce.alias', 'bounce.test')
        cls.env['ir.config_parameter'].set_param('mail.catchall.domain', 'test.com')
        cls.env['ir.config_parameter'].set_param('mail.catchall.alias', 'catchall.test')

    # --------------------------------------------------
    # Base low-level tests
    # --------------------------------------------------

    @mute_logger('odoo.addons.mail.models.mail_thread', 'odoo.models')
    def test_message_process_alias_basic(self):
        """ Test details of created message going through mailgateway """
        record = self.format_and_process(MAIL_TEMPLATE, self.email_from, 'groups@test.com', subject='Specific')

        # Test: one group created by mailgateway administrator as user_id is not set
        self.assertEqual(len(record), 1, 'message_process: a new mail.test should have been created')
        res = record.get_metadata()[0].get('create_uid') or [None]
        self.assertEqual(res[0], self.env.uid)

        # Test: one message that is the incoming email
        self.assertEqual(len(record.message_ids), 1)
        msg = record.message_ids[0]
        self.assertEqual(msg.subject, 'Specific')
        self.assertIn('Please call me as soon as possible this afternoon!', msg.body)
        self.assertEqual(msg.message_type, 'email')
        self.assertEqual(msg.subtype_id, self.env.ref('mail.mt_comment'))

    @mute_logger('odoo.addons.mail.models.mail_thread')
    def test_message_process_cid(self):
        record = self.format_and_process(test_mail_data.MAIL_MULTIPART_IMAGE, self.email_from, 'groups@test.com')
        message = record.message_ids[0]
        for attachment in message.attachment_ids:
            self.assertIn('/web/image/%s' % attachment.id, message.body)
        self.assertEqual(
            set(message.attachment_ids.mapped('name')),
            set(['rosaçée.gif', 'verte!µ.gif', 'orangée.gif']))

    def test_message_process_followers(self):
        pass
        # TODO : the author of a message post should be added as follower
        # currently it is not the case as otherwise Administrator would be follower of a lot of stuff
        # this is a bug with mail_create_nosubscribe -> should be changed in master
        # self.assertEqual(record.message_partner_ids, self.partner_1,
        #                  'message_process: recognized email -> added as follower')

        # TODO : the author of a message post on mail.test should not be added as follower
        # Test: author (and not recipient) added as follower
        # self.assertEqual(self.test_public.message_partner_ids, self.partner_1 | self.partner_2,
        #                  'message_process: after reply, group should have 2 followers')
        # self.assertEqual(self.test_public.message_channel_ids, self.env['mail.test'],
        #                  'message_process: after reply, group should have 2 followers (0 channels)')

    # --------------------------------------------------
    # Author recognition
    # --------------------------------------------------

    @mute_logger('odoo.addons.mail.models.mail_thread', 'odoo.models')
    def test_message_process_email_email_from(self):
        """ Incoming email: not recognized author: email_from, no author_id, no followers """
        record = self.format_and_process(MAIL_TEMPLATE, self.email_from, 'groups@test.com')
        self.assertFalse(record.message_ids[0].author_id, 'message_process: unrecognized email -> no author_id')
        self.assertEqual(record.message_ids[0].email_from, self.email_from)
        self.assertEqual(len(record.message_partner_ids), 0,
                         'message_process: newly create group should not have any follower')

    @mute_logger('odoo.addons.mail.models.mail_thread', 'odoo.models')
    def test_message_process_email_author(self):
        """ Incoming email: recognized author: email_from, author_id, added as follower """
        record = self.format_and_process(MAIL_TEMPLATE, self.partner_1.email_formatted, 'groups@test.com', subject='Test1')

        self.assertEqual(record.message_ids[0].author_id, self.partner_1,
                         'message_process: recognized email -> author_id')
        self.assertEqual(record.message_ids[0].email_from, self.partner_1.email_formatted)
        self.assertEqual(len(self._mails), 0, 'No notification / bounce should be sent')

        # Email recognized if partner has a formatted email
        self.partner_1.write({'email': {'Valid Lelitre <%s>' % self.partner_1.email}})
        record = self.format_and_process(MAIL_TEMPLATE, self.partner_1.email, 'groups@test.com', subject='Test2')

        self.assertEqual(record.message_ids[0].author_id, self.partner_1,
                         'message_process: recognized email -> author_id')
        self.assertEqual(record.message_ids[0].email_from, self.partner_1.email)
        self.assertEqual(len(self._mails), 0, 'No notification / bounce should be sent')

    @mute_logger('odoo.addons.mail.models.mail_thread', 'odoo.models')
    def test_message_process_email_partner_find(self):
        """ Finding the partner based on email, based on partner / user / follower """
        self.alias.write({'alias_force_thread_id': self.test_record.id})
        from_1 = self.env['res.partner'].create({'name': 'Brice Denisse', 'email': 'from.test@example.com'})

        self.format_and_process(MAIL_TEMPLATE, from_1.email_formatted, 'groups@test.com')
        self.assertEqual(self.test_record.message_ids[0].author_id, from_1)
        self.test_record.message_unsubscribe([from_1.id])

        from_2 = mail_new_test_user(self.env, login='B', groups='base.group_user', name='User Denisse', email='from.test@example.com')

        self.format_and_process(MAIL_TEMPLATE, from_1.email_formatted, 'groups@test.com')
        self.assertEqual(self.test_record.message_ids[0].author_id, from_2.partner_id)
        self.test_record.message_unsubscribe([from_2.partner_id.id])

        from_3 = self.env['res.partner'].create({'name': 'FOllower Denisse', 'email': 'from.test@example.com'})
        self.test_record.message_subscribe([from_3.id])

        self.format_and_process(MAIL_TEMPLATE, from_1.email_formatted, 'groups@test.com')
        self.assertEqual(self.test_record.message_ids[0].author_id, from_3)

    @mute_logger('odoo.addons.mail.models.mail_thread', 'odoo.models')
    def test_message_process_email_author_exclude_alias(self):
        """ Do not set alias as author to avoid including aliases in discussions """
        from_1 = self.env['res.partner'].create({'name': 'Brice Denisse', 'email': 'from.test@test.com'})
        self.env['mail.alias'].create({
            'alias_name': 'from.test',
            'alias_model_id': self.env['ir.model']._get('mail.test.gateway').id
        })

        record = self.format_and_process(MAIL_TEMPLATE, from_1.email_formatted, 'groups@test.com')
        self.assertFalse(record.message_ids[0].author_id)
        self.assertEqual(record.message_ids[0].email_from, from_1.email_formatted)

    # --------------------------------------------------
    # Alias configuration
    # --------------------------------------------------

    @mute_logger('odoo.addons.mail.models.mail_thread', 'odoo.models')
    def test_message_process_alias_user_id(self):
        """ Test alias ownership """
        self.alias.write({'alias_user_id': self.user_employee.id})

        record = self.format_and_process(MAIL_TEMPLATE, self.email_from, 'groups@test.com')
        self.assertEqual(len(record), 1)
        res = record.get_metadata()[0].get('create_uid') or [None]
        self.assertEqual(res[0], self.user_employee.id)

    @mute_logger('odoo.addons.mail.models.mail_thread', 'odoo.models')
    def test_message_process_alias_everyone(self):
        """ Incoming email: everyone: new record + message_new """
        self.alias.write({'alias_contact': 'everyone'})

        record = self.format_and_process(MAIL_TEMPLATE, self.email_from, 'groups@test.com', subject='Specific')
        self.assertEqual(len(record), 1)
        self.assertEqual(len(record.message_ids), 1)

    @mute_logger('odoo.addons.mail.models.mail_thread', 'odoo.models.unlink', 'odoo.addons.mail.models.mail_mail')
    def test_message_process_alias_partners_bounce(self):
        """ Incoming email from an unknown partner on a Partners only alias -> bounce + test bounce email """
        self.alias.write({'alias_contact': 'partners'})

        # Test: no group created, email bounced
        record = self.format_and_process(MAIL_TEMPLATE, self.email_from, 'groups@test.com', subject='Should Bounce')
        self.assertFalse(record)
        self.assertEqual(len(self._mails), 1,
                         'message_process: incoming email on Partners alias should send a bounce email')
        # Test bounce email
        self.assertEqual(self._mails[0].get('subject'), 'Re: Should Bounce')
        self.assertEqual(self._mails[0].get('email_to')[0], 'whatever-2a840@postmaster.twitter.com')
        self.assertEqual(self._mails[0].get('email_from'), formataddr(('MAILER-DAEMON', 'bounce.test@test.com')))

    @mute_logger('odoo.addons.mail.models.mail_thread', 'odoo.models.unlink', 'odoo.addons.mail.models.mail_mail')
    def test_message_process_alias_followers_bounce(self):
        """ Incoming email from unknown partner / not follower partner on a Followers only alias -> bounce """
        self.alias.write({
            'alias_contact': 'followers',
            'alias_parent_model_id': self.env['ir.model']._get('mail.test.gateway').id,
            'alias_parent_thread_id': self.test_record.id,
        })

        # Test: unknown on followers alias -> bounce
        record = self.format_and_process(MAIL_TEMPLATE, self.email_from, 'groups@test.com', subject='Should Bounce')
        self.assertFalse(record, 'message_process: should have bounced')
        self.assertEqual(len(self._mails), 1,
                         'message_process: incoming email on Followers alias should send a bounce email')
        self.assertEqual(self._mails[0].get('subject'), 'Re: Should Bounce')
        self.assertEqual(self._mails[0].get('email_to')[0], 'whatever-2a840@postmaster.twitter.com')
        self.assertEqual(self._mails[0].get('email_from'), formataddr(('MAILER-DAEMON', 'bounce.test@test.com')))

        # Test: partner on followers alias -> bounce
        self._init_mock_build_email()
        record = self.format_and_process(MAIL_TEMPLATE, self.partner_1.email_formatted, 'groups@test.com', subject='Should Bounce')
        self.assertFalse(record, 'message_process: should have bounced')
        self.assertEqual(len(self._mails), 1,
                         'message_process: incoming email on Followers alias should send a bounce email')
        self.assertEqual(self._mails[0].get('subject'), 'Re: Should Bounce')
        self.assertEqual(self._mails[0].get('email_to')[0], 'whatever-2a840@postmaster.twitter.com')
        self.assertEqual(self._mails[0].get('email_from'), formataddr(('MAILER-DAEMON', 'bounce.test@test.com')))

    @mute_logger('odoo.addons.mail.models.mail_thread', 'odoo.models')
    def test_message_process_alias_partner(self):
        """ Incoming email from a known partner on a Partners alias -> ok (+ test on alias.user_id) """
        self.alias.write({'alias_contact': 'partners'})
        record = self.format_and_process(MAIL_TEMPLATE, self.partner_1.email_formatted, 'groups@test.com')

        # Test: one group created by alias user
        self.assertEqual(len(record), 1)
        self.assertEqual(len(record.message_ids), 1)

    @mute_logger('odoo.addons.mail.models.mail_thread', 'odoo.models')
    def test_message_process_alias_followers(self):
        """ Incoming email from a parent document follower on a Followers only alias -> ok """
        self.alias.write({
            'alias_contact': 'followers',
            'alias_parent_model_id': self.env['ir.model']._get('mail.test.gateway').id,
            'alias_parent_thread_id': self.test_record.id,
        })
        self.test_record.message_subscribe(partner_ids=[self.partner_1.id])
        record = self.format_and_process(MAIL_TEMPLATE, self.partner_1.email_formatted, 'groups@test.com')

        # Test: one group created by Raoul (or Sylvie maybe, if we implement it)
        self.assertEqual(len(record), 1)

    @mute_logger('odoo.addons.mail.models.mail_thread', 'odoo.models.unlink', 'odoo.addons.mail.models.mail_mail')
    def test_message_process_alias_update(self):
        """ Incoming email update discussion + notification email """
        self.alias.write({'alias_force_thread_id': self.test_record.id})

        self.test_record.message_subscribe(partner_ids=[self.partner_1.id])
        record = self.format_and_process(
            MAIL_TEMPLATE, self.email_from, 'groups@test.com>',
            msg_id='<1198923581.41972151344608186799.JavaMail.diff1@agrolait.com>', subject='Re: cats')

        # Test: no new group + new message
        self.assertFalse(record, 'message_process: alias update should not create new records')
        self.assertEqual(len(self.test_record.message_ids), 2)
        # Test: sent emails: 1 (Sylvie copy of the incoming email)
        self.assertEmails(False, self.partner_1, email_from=self.email_from)

    # --------------------------------------------------
    # Email Management
    # --------------------------------------------------

    @mute_logger('odoo.addons.mail.models.mail_thread')
    def test_message_process_write_to_catchall(self):
        """ Writing directly to catchall should bounce """

        # Test: no group created, email bounced
        record = self.format_and_process(MAIL_TEMPLATE, self.partner_1.email_formatted, '"My Super Catchall" <catchall.test@test.com>', subject='Should Bounce')
        self.assertFalse(record)
        self.assertEqual(len(self._mails), 1,
                         'message_process: writing directly to catchall should bounce')
        # Test bounce email
        self.assertEqual(self._mails[0].get('subject'), 'Re: Should Bounce')
        self.assertEqual(self._mails[0].get('email_to')[0], 'whatever-2a840@postmaster.twitter.com')
        self.assertEqual(self._mails[0].get('email_from'), formataddr(('MAILER-DAEMON', 'bounce.test@test.com')))

    @mute_logger('odoo.addons.mail.models.mail_thread')
    def test_message_process_bounce(self):
        """ Writing to bounce alias is considered as a bounce """
        self.assertEqual(self.partner_1.message_bounce, 0)

        bounced_mail_id = 4442
        bounce_email_to = '%s+%s-%s-%s@%s' % ('bounce.test', bounced_mail_id, self.test_record._name, self.test_record.id, 'test.com')
        record = self.format_and_process(test_mail_data.MAIL_BOUNCE, self.partner_1.email_formatted, bounce_email_to, subject='Undelivered Mail Returned to Sender')
        self.assertFalse(record)
        # self.assertEqual(self.partner_1.message_bounce, 1)  # TDE FIXME: smart test crashing in current master

    @mute_logger('odoo.addons.mail.models.mail_thread')
    def test_message_process_bounce_whatever_from(self):
        """ Writing to bounce alias is considered as a bounce """
        self.assertEqual(self.partner_1.message_bounce, 0)

        bounced_mail_id = 4442
        bounce_email_to = '%s+%s-%s-%s@%s' % ('bounce.test', bounced_mail_id, self.test_record._name, self.test_record.id, 'test.com')
        record = self.format_and_process(test_mail_data.MAIL_BOUNCE, 'Whatever <what@ever.com>', bounce_email_to, subject='Undelivered Mail Returned to Sender')
        self.assertFalse(record)
        # self.assertEqual(self.partner_1.message_bounce, 1)  # TDE FIXME: smart test crashing in current master

    @mute_logger('odoo.addons.mail.models.mail_thread') 
    def test_message_process_bounce_multipart_report(self):
        """ Multipart/report emails should be considered as bounce """
        self.assertEqual(self.partner_1.message_bounce, 0)
        record = self.format_and_process(test_mail_data.MAIL_BOUNCE, 'Whatever <what@ever.com>', 'groups@test.com', subject='Undelivered Mail Returned to Sender')
        self.assertFalse(record)
        # self.assertEqual(self.partner_1.message_bounce, 1)  # TDE FIXME: smart test crashing in current master

    @mute_logger('odoo.addons.mail.models.mail_thread') 
    def test_message_process_bounce_mailer_demon(self):
        """ Multipart/report emails should be considered as bounce """
        self.assertEqual(self.partner_1.message_bounce, 0)
        record = self.format_and_process(MAIL_TEMPLATE, 'MAILER-DAEMON@example.com', 'groups@test.com', subject='Undelivered Mail Returned to Sender')
        self.assertFalse(record)
        # self.assertEqual(self.partner_1.message_bounce, 1)  # TDE FIXME: smart test crashing in current master

    # --------------------------------------------------
    # Thread formation
    # --------------------------------------------------

    @mute_logger('odoo.addons.mail.models.mail_thread', 'odoo.models')
    def test_message_process_in_reply_to(self):
        """ Incoming email using in-rely-to should go into the right destination even with a wrong destination """
        init_msg_count = len(self.test_record.message_ids)
        self.format_and_process(
            MAIL_TEMPLATE, 'valid.other@gmail.com', 'erroneous@test.com>',
            subject='Re: news', extra='In-Reply-To:\r\n\t%s\n' % self.fake_email.message_id)

        self.assertEqual(len(self.test_record.message_ids), init_msg_count + 1)
        self.assertEqual(self.fake_email.child_ids, self.test_record.message_ids[0])

    @mute_logger('odoo.addons.mail.models.mail_thread', 'odoo.models')
    def test_message_process_references(self):
        """ Incoming email using references should go into the right destination even with a wrong destination """
        init_msg_count = len(self.test_record.message_ids)
        self.format_and_process(
            MAIL_TEMPLATE, self.email_from, 'erroneous@test.com',
            extra='References: <2233@a.com>\r\n\t<3edss_dsa@b.com> %s' % self.fake_email.message_id)

        self.assertEqual(len(self.test_record.message_ids), init_msg_count + 1)
        self.assertEqual(self.fake_email.child_ids, self.test_record.message_ids[0])

    @mute_logger('odoo.addons.mail.models.mail_thread')
    def test_message_process_references_external(self):
        """ Incoming email being a reply to an external email processed by odoo should update thread accordingly """
        new_message_id = '<ThisIsTooMuchFake.MonsterEmail.789@agrolait.com>'
        self.fake_email.write({
            'message_id': new_message_id
        })
        init_msg_count = len(self.test_record.message_ids)
        self.format_and_process(
            MAIL_TEMPLATE, self.email_from, 'erroneous@test.com',
            extra='References: <2233@a.com>\r\n\t<3edss_dsa@b.com> %s' % self.fake_email.message_id)

        self.assertEqual(len(self.test_record.message_ids), init_msg_count + 1)
        self.assertEqual(self.fake_email.child_ids, self.test_record.message_ids[0])

    @mute_logger('odoo.addons.mail.models.mail_thread')
    def test_message_process_references_forward(self):
        """ Incoming email using references but with alias forward should not go into references destination """
        self.env['mail.alias'].create({
            'alias_name': 'test.alias',
            'alias_user_id': False,
            'alias_model_id': self.env['ir.model']._get('mail.test').id,
            'alias_contact': 'everyone',
        })
        init_msg_count = len(self.test_record.message_ids)
        res_test = self.format_and_process(
            MAIL_TEMPLATE, self.email_from, 'test.alias@test.com',
            subject='My Dear Forward', extra='References: <2233@a.com>\r\n\t<3edss_dsa@b.com> %s' % self.fake_email.message_id,
            target_model='mail.test')

        self.assertEqual(len(self.test_record.message_ids), init_msg_count)
        self.assertEqual(len(self.fake_email.child_ids), 0)
        self.assertEqual(res_test.name, 'My Dear Forward')
        self.assertEqual(len(res_test.message_ids), 1)

    @mute_logger('odoo.addons.mail.models.mail_thread', 'odoo.models')
    def test_message_process_references_forward_same_model(self):
        """ Incoming email using references but with alias forward on same model should be considered as a reply """
        self.env['mail.alias'].create({
            'alias_name': 'test.alias',
            'alias_user_id': False,
            'alias_model_id': self.env['ir.model']._get('mail.test.gateway').id,
            'alias_contact': 'everyone',
        })
        init_msg_count = len(self.test_record.message_ids)
        res_test = self.format_and_process(
            MAIL_TEMPLATE, self.email_from, 'test.alias@test.com',
            subject='My Dear Forward', extra='References: <2233@a.com>\r\n\t<3edss_dsa@b.com> %s' % self.fake_email.message_id,
            target_model='mail.test')

        self.assertEqual(len(self.test_record.message_ids), init_msg_count + 1)
        self.assertEqual(len(self.fake_email.child_ids), 1)
        self.assertFalse(res_test)

    @mute_logger('odoo.addons.mail.models.mail_thread', 'odoo.models')
    def test_message_process_references_forward_cc(self):
        """ Incoming email using references but with alias forward in CC should be considered as a repy (To > Cc) """
        self.env['mail.alias'].create({
            'alias_name': 'test.alias',
            'alias_user_id': False,
            'alias_model_id': self.env['ir.model']._get('mail.test').id,
            'alias_contact': 'everyone',
        })
        init_msg_count = len(self.test_record.message_ids)
        res_test = self.format_and_process(
            MAIL_TEMPLATE, self.email_from, 'catchall.test@test.com', cc='test.alias@test.com',
            subject='My Dear Forward', extra='References: <2233@a.com>\r\n\t<3edss_dsa@b.com> %s' % self.fake_email.message_id,
            target_model='mail.test')

        self.assertEqual(len(self.test_record.message_ids), init_msg_count + 1)
        self.assertEqual(len(self.fake_email.child_ids), 1)
        self.assertFalse(res_test)

    @mute_logger('odoo.addons.mail.models.mail_thread', 'odoo.models', 'odoo.addons.mail.models.mail_mail')
    def test_message_process_reply_to_new_thread(self):
        """ Test replies not being considered as replies but use destination information instead (aka, mass post + specific reply to using aliases) """
        first_record = self.env['mail.test.simple'].with_user(self.user_employee).create({'name': 'Replies to Record'})
        record_msg = first_record.message_post(
            subject='Discussion',
            no_auto_thread=False,
            subtype='mail.mt_comment',
        )
        self.assertEqual(record_msg.reply_to, formataddr(('%s %s' % (self.user_employee.company_id.name, first_record.name), '%s@%s' % ('catchall.test', 'test.com'))))
        mail_msg = first_record.message_post(
            subject='Replies to Record',
            reply_to='groups@test.com',
            no_auto_thread=True,
            subtype='mail.mt_comment',
        )
        self.assertEqual(mail_msg.reply_to, 'groups@test.com')

        # reply to mail but should be considered as a new mail for alias
        msgID = '<this.is.duplicate.test@iron.sky>'
        res_test = self.format_and_process(
            MAIL_TEMPLATE, self.email_from, record_msg.reply_to, cc='',
            subject='Re: Replies to Record', extra='In-Reply-To: %s' % record_msg.message_id,
            msg_id=msgID, target_model='mail.test.simple')
        incoming_msg = self.env['mail.message'].search([('message_id', '=', msgID)])
        self.assertFalse(res_test)
        self.assertEqual(incoming_msg.model, 'mail.test.simple')
        self.assertEqual(incoming_msg.parent_id, first_record.message_ids[-1])
        self.assertTrue(incoming_msg.res_id == first_record.id)

        # reply to mail but should be considered as a new mail for alias
        msgID = '<this.is.for.testing@iron.sky>'
        res_test = self.format_and_process(
            MAIL_TEMPLATE, self.email_from, mail_msg.reply_to, cc='',
            subject='Re: Replies to Record', extra='In-Reply-To: %s' % mail_msg.message_id,
            msg_id=msgID, target_model='mail.test.gateway')
        incoming_msg = self.env['mail.message'].search([('message_id', '=', msgID)])
        self.assertEqual(len(res_test), 1)
        self.assertEqual(res_test.name, 'Re: Replies to Record')
        self.assertEqual(incoming_msg.model, 'mail.test.gateway')
        self.assertFalse(incoming_msg.parent_id)
        self.assertTrue(incoming_msg.res_id == res_test.id)

    # --------------------------------------------------
    # Thread formation: mail gateway corner cases
    # --------------------------------------------------

    @mute_logger('odoo.addons.mail.models.mail_thread', 'odoo.models')
    def test_message_process_extra_model_res_id(self):
        """ Incoming email with ref holding model / res_id but that does not match any message in the thread: must raise since OpenERP saas-3 """
        self.assertRaises(ValueError,
                          self.format_and_process, MAIL_TEMPLATE,
                          self.partner_1.email_formatted, 'noone@test.com', subject='spam',
                          extra='In-Reply-To: <12321321-openerp-%d-mail.test.gateway@%s>' % (self.test_record.id, socket.gethostname()))

        # when 6.1 messages are present, compat mode is available
        # Odoo 10 update: compat mode has been removed and should not work anymore
        self.fake_email.write({'message_id': False})
        # Do: compat mode accepts partial-matching emails
        self.assertRaises(
            ValueError,
            self.format_and_process, MAIL_TEMPLATE,
            self.partner_1.email_formatted, 'noone@test.com>', subject='spam',
            extra='In-Reply-To: <12321321-openerp-%d-mail.test.gateway@%s>' % (self.test_record.id, socket.gethostname()))

        # Test created messages
        self.assertEqual(len(self.test_record.message_ids), 1)
        self.assertEqual(len(self.test_record.message_ids[0].child_ids), 0)

    @mute_logger('odoo.addons.mail.models.mail_thread', 'odoo.models')
    def test_message_process_duplicate(self):
        """ Duplicate emails (same message_id) are not processed """
        self.alias.write({'alias_force_thread_id': self.test_record.id,})

        # Post a base message
        record = self.format_and_process(MAIL_TEMPLATE, self.email_from, 'groups@test.com', subject='Re: super cats', msg_id='<123?456.diff1@agrolait.com>')
        self.assertFalse(record)
        self.assertEqual(len(self.test_record.message_ids), 2)

        # Do: due to some issue, same email goes back into the mailgateway
        record = self.format_and_process(
            MAIL_TEMPLATE, self.email_from, 'groups@test.com', subject='Re: news',
            msg_id='<123?456.diff1@agrolait.com>', extra='In-Reply-To: <1198923581.41972151344608186799.JavaMail.diff1@agrolait.com>\n')
        self.assertFalse(record)
        self.assertEqual(len(self.test_record.message_ids), 2)

        # Test: message_id is still unique
        no_of_msg = self.env['mail.message'].search_count([('message_id', 'ilike', '<123?456.diff1@agrolait.com>')])
        self.assertEqual(no_of_msg, 1,
                         'message_process: message with already existing message_id should not have been duplicated')

    @mute_logger('odoo.addons.mail.models.mail_thread', 'odoo.models')
    def test_message_process_crash_wrong_model(self):
        """ Incoming email with model that does not accepts incoming emails must raise """
        self.assertRaises(ValueError,
                          self.format_and_process,
                          MAIL_TEMPLATE, self.email_from, 'noone@test.com',
                          subject='spam', extra='', model='res.country')

    @mute_logger('odoo.addons.mail.models.mail_thread', 'odoo.models')
    def test_message_process_crash_no_data(self):
        """ Incoming email without model and without alias must raise """
        self.assertRaises(ValueError,
                          self.format_and_process,
                          MAIL_TEMPLATE, self.email_from, 'noone@test.com',
                          subject='spam', extra='')

    @mute_logger('odoo.addons.mail.models.mail_thread', 'odoo.models')
    def test_message_process_fallback(self):
        """ Incoming email with model that accepting incoming emails as fallback """
        record = self.format_and_process(
            MAIL_TEMPLATE, self.email_from, 'noone@test.com',
            subject='Spammy', extra='', model='mail.test.gateway')
        self.assertEqual(len(record), 1)
        self.assertEqual(record.name, 'Spammy')
        self.assertEqual(record._name, 'mail.test.gateway')
