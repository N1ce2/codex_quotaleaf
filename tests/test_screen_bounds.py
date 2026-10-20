import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from PyQt5.QtCore import QPoint,QRect,QSize,Qt,QEvent
from PyQt5.QtGui import QMouseEvent
from PyQt5.QtWidgets import QApplication
from screen_bounds import bounded_position,fitting_scale,screen_index
from quota_card import QuotaCard,DEFAULTS


class ScreenGeometry(unittest.TestCase):
    def test_all_four_edges_and_corners(self):
        area=QRect(0,0,1920,1040);size=QSize(360,278)
        for point,expected in [(QPoint(-30,200),QPoint(0,200)),(QPoint(1800,200),QPoint(1560,200)),
                (QPoint(100,-100),QPoint(100,0)),(QPoint(100,1000),QPoint(100,762)),
                (QPoint(-999,-999),QPoint(0,0)),(QPoint(9999,9999),QPoint(1560,762))]:
            self.assertEqual(bounded_position(point,size,area),expected)

    def test_negative_monitor_and_taskbar_offset(self):
        area=QRect(-1920,-1080,1920,1040);size=QSize(468,361)
        position=bounded_position(QPoint(100,50),size,area)
        self.assertEqual(position,QPoint(-468,-401))
        self.assertTrue(area.contains(QRect(position,size)))
        area=QRect(40,60,1880,1020)
        self.assertEqual(bounded_position(QPoint(-1,-1),size,area),QPoint(40,60))

    def test_in_bounds_is_unchanged_and_snap_uses_real_boundary(self):
        area=QRect(0,0,1000,800);size=QSize(144,58)
        self.assertEqual(bounded_position(QPoint(300,400),size,area,8),QPoint(300,400))
        self.assertEqual(bounded_position(QPoint(3,740),size,area,8),QPoint(0,742))

    def test_all_modes_and_scales_fit_small_monitor(self):
        area=QRect(-800,0,800,440)
        for w,h in [(360,278),(360,210),(144,58)]:
            for requested in [50,100,130,175,250]:
                scale=fitting_scale(requested,w,h,area)
                size=QSize(round(w*scale/100),round(h*scale/100))
                point=bounded_position(QPoint(10000,-10000),size,area)
                self.assertTrue(area.contains(QRect(point,size)))
                self.assertLessEqual(scale,requested)

    def test_monitor_selection_uses_overlap_or_nearest(self):
        areas=[QRect(0,0,1920,1040),QRect(-1920,0,1920,1040)]
        self.assertEqual(screen_index(QRect(-300,20,200,100),areas),1)
        self.assertEqual(screen_index(QRect(-4000,20,200,100),areas),1)
        self.assertEqual(screen_index(QRect(10000,20,200,100),areas),0)


class CardBoundaries(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])

    def setUp(self):
        self.card=QuotaCard(preview=True);self.card.settings.update(DEFAULTS);self.card.apply_settings(save=False)

    def tearDown(self):self.card.close()

    def test_partial_offscreen_startup_position_is_recovered(self):
        area=QApplication.primaryScreen().availableGeometry()
        self.card.constrain_to_screen(QPoint(area.right()-10,area.bottom()-10))
        self.assertTrue(area.contains(self.card.geometry()))

    def test_expanding_mini_and_scaling_at_edge_stays_visible(self):
        area=QApplication.primaryScreen().availableGeometry()
        self.card.settings['mini']=True;self.card.apply_settings(save=False)
        self.card.move(area.right()-self.card.width()+1,area.bottom()-self.card.height()+1)
        for scale,mini in [(130,False),(250,False),(100,True)]:
            self.card.settings.update(scale=scale,mini=mini);self.card.apply_settings(save=False)
            self.assertTrue(area.contains(self.card.geometry()))

    def test_drag_handler_rejects_out_of_bounds_movement(self):
        area=QApplication.primaryScreen().availableGeometry()
        self.card.drag_start=QPoint(10,10)
        for global_point in [QPoint(-10000,-10000),QPoint(10000,10000)]:
            event=QMouseEvent(QEvent.MouseMove,QPoint(10,10),global_point,Qt.NoButton,Qt.LeftButton,Qt.NoModifier)
            self.card.mouseMoveEvent(event)
            self.assertTrue(area.contains(self.card.geometry()))

    def test_screen_work_area_change_reclamps_and_saves(self):
        area=QApplication.primaryScreen().availableGeometry()
        self.card.move(area.right(),area.bottom())
        with patch.object(self.card,'save') as save:
            self.card.screen_layout_changed()
            self.assertTrue(area.contains(self.card.geometry()));save.assert_called_once()
