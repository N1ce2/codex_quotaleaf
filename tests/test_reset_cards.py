import copy
import os
from pathlib import Path
import sys
import time
import unittest

os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from PyQt5.QtWidgets import QApplication
from quota_card import QuotaCard,RoundedMenu
from reset_cards import reset_inventory,reset_usable,populate_reset_menu


def fixture():
    return {'rateLimits':{'primary':{'usedPercent':50,'windowDurationMins':300,'resetsAt':time.time()+3600}},
            'rateLimitResetCredits':{'availableCount':3,'credits':[
                {'id':'test-only-reset-1','status':'available','resetType':'codexRateLimits',
                 'title':'Rate-limit reset','expiresAt':time.time()+3600}]}}


class ResetCards(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])

    def setUp(self):
        self.card=QuotaCard(preview=True)
        self.card.client.ready=True
        self.card.client.reset_keys={}
        self.card.accept_usage(fixture())
        self.sent=[]
        # All RPC writes are replaced: these tests can never redeem a real card.
        self.card.client.send=lambda message:self.sent.append(copy.deepcopy(message))

    def tearDown(self):
        if hasattr(self,'menu'):self.menu.close()
        self.card.close()

    def make_menu(self):
        self.menu=RoundedMenu(self.card)
        populate_reset_menu(self.menu,self.card,RoundedMenu)
        return self.menu

    def test_count_is_authoritative_and_unknown_is_unknown(self):
        count,cards=reset_inventory(fixture())
        self.assertEqual(count,3);self.assertEqual(len(cards),1)
        self.assertEqual(reset_inventory({}),(None,None))
        self.assertEqual(reset_inventory({'rateLimitResetCredits':None}),(None,None))
        self.assertEqual(reset_inventory({'rateLimitResetCredits':{'availableCount':0,'credits':[]}}),(0,[]))

    def test_invalid_expired_and_used_cards_are_unusable(self):
        card=fixture()['rateLimitResetCredits']['credits'][0]
        self.assertTrue(reset_usable(card))
        for change in ({'expiresAt':1},{'status':'redeemed'},{'id':''},{'resetType':'unknown'},{'expiresAt':'bad'}):
            self.assertFalse(reset_usable({**card,**change}))

    def test_partial_notifications_keep_cards_without_faking_freshness(self):
        stamp=self.card.cards_synced_at
        self.card.accept_usage({'rateLimits':{'primary':{'usedPercent':60}}},partial=True)
        self.assertEqual(reset_inventory(self.card.payload)[0],3)
        self.assertEqual(stamp,self.card.cards_synced_at)

    def test_cancel_confirmation_never_sends_redemption(self):
        menu=self.make_menu()
        child=menu.credit_actions[0][1].menu()
        child.actions()[-1].trigger()
        self.assertEqual(self.sent,[])

    def test_state_is_rechecked_after_confirmation(self):
        menu=self.make_menu()
        self.card.payload['rateLimitResetCredits']['credits']=[]
        menu.credit_actions[0][2].trigger()
        self.assertEqual(self.sent,[])

    def test_confirmed_selection_sends_only_the_selected_card(self):
        menu=self.make_menu()
        credit=fixture()['rateLimitResetCredits']['credits'][0]
        menu.credit_actions[0][2].trigger()
        self.assertEqual(len(self.sent),1)
        self.assertEqual(self.sent[0]['params']['creditId'],credit['id'])
        self.assertEqual(self.sent[0]['method'],'account/rateLimitResetCredit/consume')

    def test_count_without_details_does_not_auto_select_a_card(self):
        payload=fixture();payload['rateLimitResetCredits']['credits']=None
        self.card.accept_usage(payload)
        menu=self.make_menu()
        self.assertEqual(len(menu.credit_actions),0)
        self.assertEqual(self.sent,[])

    def test_offline_cannot_redeem(self):
        self.card.error=True
        credit=fixture()['rateLimitResetCredits']['credits'][0]
        self.assertFalse(self.card.client.consume_reset(credit));self.assertEqual(self.sent,[])

    def test_duplicate_click_blocked_and_uncertain_retry_reuses_key(self):
        client=self.card.client;credit=fixture()['rateLimitResetCredits']['credits'][0]
        self.assertTrue(client.consume_reset(credit))
        first=self.sent[-1]
        self.assertFalse(client.consume_reset(credit))
        client.reset_timeout()
        self.assertTrue(self.card.reset_refresh_pending)
        client.pending=None;self.card.accept_usage(fixture())
        self.assertTrue(client.consume_reset(credit))
        self.assertEqual(first['params'],self.sent[-1]['params'])
        self.assertEqual(self.sent[-1]['method'],'account/rateLimitResetCredit/consume')

    def test_success_reads_fresh_limits_instead_of_fabricating_them(self):
        client=self.card.client;credit=fixture()['rateLimitResetCredits']['credits'][0]
        client.consume_reset(credit)
        client.finish_reset({'result':{'outcome':'reset'}})
        self.assertEqual(self.card.payload['rateLimits']['primary']['usedPercent'],50)
        self.assertTrue(self.card.reset_refresh_pending)
        self.assertEqual(self.sent[-1]['method'],'account/rateLimits/read')

    def test_nothing_to_reset_allows_a_new_logical_attempt(self):
        client=self.card.client;credit=fixture()['rateLimitResetCredits']['credits'][0]
        client.consume_reset(credit);old_key=self.sent[-1]['params']['idempotencyKey']
        client.finish_reset({'result':{'outcome':'nothingToReset'}})
        client.pending=None;self.card.accept_usage(fixture())
        client.consume_reset(credit)
        self.assertNotEqual(old_key,self.sent[-1]['params']['idempotencyKey'])


if __name__=='__main__':unittest.main()
