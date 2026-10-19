"""A local Windows quota widget. Reads Codex account limits; never sends turns."""
from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import uuid

from reset_cards import reset_inventory, reset_usable, ResetCardsDialog

from PyQt5.QtCore import (Qt, QProcess, QProcessEnvironment, QTimer, QRectF,
                          QPoint, QPointF, QSize, QStandardPaths, QLockFile)
from PyQt5.QtGui import (QColor, QFont, QIcon, QPainter, QPainterPath, QPen,
                         QPixmap, QLinearGradient)
from PyQt5.QtWidgets import (QApplication, QWidget, QDialog, QVBoxLayout,
    QHBoxLayout, QLabel, QPushButton, QCheckBox, QComboBox, QSlider,
    QFileDialog, QColorDialog, QMenu, QSystemTrayIcon, QFrame, QMessageBox, QSpinBox)

BASE = Path(__file__).resolve().parent
STATE = BASE / 'state'
DEFAULTS = dict(x=None, y=90, scale=100, radius=24, opacity=92, image='',
    image_shade=52, accent='#a4d8c1', theme='石墨黑', topmost=True,
    locked=False, compact=False, show_reset=True, interval=3,
    autostart=False, bucket='codex', settings_version=2)
PALETTES = {
    '石墨黑': ('#292929', '#f4f4f2', '#a8aaa8', '#464846'),
    '午夜蓝': ('#18232e', '#eef4fb', '#9fadb9', '#344657'),
    '奶油白': ('#f7f5ef', '#242923', '#68716a', '#dcded5'),
}


def load_settings():
    settings = dict(DEFAULTS)
    try:
        raw = json.loads((STATE / 'settings.json').read_text('utf-8'))
        settings.update({k: v for k, v in raw.items() if k in DEFAULTS})
        if raw.get('settings_version', 1) < 2:
            if raw.get('interval', 60) == 60:
                settings['interval'] = 3
            if 'scale' not in raw:
                settings['scale'] = round(raw.get('width', 360) / 360 * 100)
            settings['settings_version'] = 2
    except (OSError, ValueError, TypeError):
        pass
    settings['scale'] = max(50, min(250, int(settings['scale'])))
    settings['radius'] = max(0, min(48, int(settings['radius'])))
    settings['opacity'] = max(20, min(100, int(settings['opacity'])))
    settings['interval'] = max(3, min(3600, int(settings['interval'])))
    if settings['theme'] not in PALETTES:
        settings['theme'] = '石墨黑'
    return settings


def atomic_json(file, value):
    STATE.mkdir(exist_ok=True)
    temp = file.with_suffix('.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), 'utf-8')
    os.replace(temp, file)


def extract_bucket(payload, preferred='codex'):
    buckets = payload.get('rateLimitsByLimitId') or {}
    if preferred in buckets:
        return buckets[preferred], list(buckets)
    if buckets:
        key = next(iter(buckets))
        return buckets[key], list(buckets)
    return payload.get('rateLimits'), []


def remaining(window):
    if not window or window.get('usedPercent') is None:
        return None
    try:
        value = float(window['usedPercent'])
        if not math.isfinite(value):
            return None
        return max(0.0, min(100.0, 100.0-value))
    except (ValueError, TypeError):
        return None


def countdown(reset, now=None):
    if not reset:
        return '重置时间未知'
    seconds = max(0, int(reset-(now or time.time())))
    if seconds == 0:
        return '待同步新额度'
    days, seconds = divmod(seconds, 86400)
    hours, seconds = divmod(seconds, 3600)
    minutes = seconds // 60
    if days:
        return f'{days} 天 {hours} 小时后重置'
    return f'{hours:02d}:{minutes:02d}:{seconds % 60:02d} 后重置'


def duration_label(window, fallback):
    mins = window.get('windowDurationMins') if window else None
    if mins == 10080:
        return '每周额度'
    if mins == 300:
        return '5 小时额度'
    if mins and mins % 1440 == 0:
        return f'{mins // 1440} 天额度'
    if mins and mins % 60 == 0:
        return f'{mins // 60} 小时额度'
    return f'{mins} 分钟额度' if mins else fallback


def find_codex():
    command = shutil.which('codex')
    if command and Path(command).suffix.lower() == '.exe':
        return command
    local = Path(os.environ.get('LOCALAPPDATA', '')) / 'OpenAI/Codex/bin'
    matches = sorted(local.glob('*/codex.exe'), key=lambda p:p.stat().st_mtime, reverse=True)
    return str(matches[0]) if matches else command


class UsageClient(QWidget):
    """Nonblocking newline-JSON RPC client using the user's existing Codex login."""
    def __init__(self, owner):
        super().__init__(owner)
        self.owner = owner
        self.proc = None
        self.buffer = b''
        self.ready = False
        self.pending = None
        self.seq = 10
        self.stopping = False
        self.reset_request = None
        self.refresh_after_reset = False
        self.reset_keys = {}
        try:
            keys=json.loads((STATE/'reset-attempts.json').read_text('utf-8'))
            if isinstance(keys,dict):self.reset_keys=keys
        except (OSError,ValueError):pass
        self.reset_deadline=QTimer(self)
        self.reset_deadline.setSingleShot(True)
        self.reset_deadline.timeout.connect(self.reset_timeout)
        self.deadline = QTimer(self)
        self.deadline.setSingleShot(True)
        self.deadline.timeout.connect(self.timeout)
        self.retry = QTimer(self)
        self.retry.setSingleShot(True)
        self.retry.timeout.connect(self.start)

    def start(self):
        if self.stopping:
            return
        if self.proc and self.proc.state() != QProcess.NotRunning:
            return
        binary = find_codex()
        if not binary:
            self.owner.connection_error('找不到 Codex。请先安装并登录 Codex。')
            return
        self.ready = False
        self.buffer = b''
        self.proc = QProcess(self)
        self.proc.setProcessEnvironment(QProcessEnvironment.systemEnvironment())
        self.proc.setWorkingDirectory(str(BASE))
        self.proc.setProgram(binary)
        self.proc.setArguments(['app-server', '--listen', 'stdio://'])
        self.proc.readyReadStandardOutput.connect(self.receive)
        # Drain diagnostic output, never persist auth tokens or private config.
        self.proc.readyReadStandardError.connect(lambda:self.proc.readAllStandardError())
        self.proc.started.connect(self.initialize)
        self.proc.errorOccurred.connect(lambda _: self.owner.connection_error('额度服务启动失败，请检查 Codex 安装。'))
        self.proc.finished.connect(self.exited)
        self.proc.start()
        self.deadline.start(35000)
        self.owner.connection_status('正在连接 Codex…')

    def send(self, message):
        if self.proc and self.proc.state() == QProcess.Running:
            self.proc.write((json.dumps(message)+'\n').encode())

    def initialize(self):
        self.send({'id':1, 'method':'initialize', 'params':{'clientInfo':{
            'name':'quota_card', 'title':'Quota Card', 'version':'1.0.0'}}})

    def receive(self):
        self.buffer += bytes(self.proc.readAllStandardOutput())
        while b'\n' in self.buffer:
            line, self.buffer = self.buffer.split(b'\n', 1)
            try:
                message = json.loads(line)
            except (ValueError, UnicodeError):
                continue
            if message.get('id') == 1:
                if message.get('error'):
                    self.owner.connection_error('Codex 初始化失败，请更新 Codex 后重试。')
                    continue
                self.ready = True
                self.deadline.stop()
                self.send({'method':'initialized','params':{}})
                self.refresh()
            elif self.reset_request and message.get('id')==self.reset_request['rpc_id']:
                self.finish_reset(message)
            elif message.get('id') == self.pending and self.pending is not None:
                self.pending = None
                self.deadline.stop()
                if self.refresh_after_reset:
                    self.refresh_after_reset=False
                    self.refresh()
                    continue
                if 'result' in message:
                    self.owner.accept_usage(message['result'])
                else:
                    error = message.get('error', {})
                    text = str(error.get('message',''))
                    lower = text.lower()
                    if any(s in lower for s in ('auth','login','unauthorized','sign in')):
                        text = '需要登录：请在 Codex 中登录 ChatGPT 账号后重试。'
                    elif any(s in lower for s in ('network','connect','timeout','request')):
                        text = '网络暂时不可用，请检查连接后刷新。'
                    else:
                        text = '暂时无法查询额度，请稍后刷新。'
                    self.owner.connection_error(text)
            elif message.get('method') == 'account/rateLimits/updated':
                self.owner.accept_usage(message.get('params', {}),partial=True)
            elif 'id' in message and 'method' in message:
                self.send({'id':message['id'],'error':{'code':-32601,'message':'Read-only quota client'}})

    def refresh(self):
        if self.pending is not None or self.reset_request is not None:
            return
        if not self.ready:
            self.start()
            return
        self.seq += 1
        self.pending = self.seq
        self.owner.connection_status('正在刷新额度…')
        self.send({'id':self.pending,'method':'account/rateLimits/read'})
        self.deadline.start(30000)

    def consume_reset(self,credit):
        count,cards=reset_inventory(self.owner.payload)
        current=next((c for c in cards or [] if c.get('id')==credit.get('id')),None)
        if (not self.ready or self.reset_request is not None or not self.owner.reset_data_fresh()
                or not count or not current or not reset_usable(current)):
            return False
        credit_id=current['id']
        key=self.reset_keys.get(credit_id) or str(uuid.uuid4())
        self.reset_keys[credit_id]=key
        # Save before sending, so an uncertain retry after restart reuses the same key.
        if not self.owner.preview:atomic_json(STATE/'reset-attempts.json',self.reset_keys)
        self.seq+=1
        self.reset_request={'rpc_id':self.seq,'credit_id':credit_id,'key':key}
        self.send({'id':self.seq,'method':'account/rateLimitResetCredit/consume',
                   'params':{'idempotencyKey':key,'creditId':credit_id}})
        self.reset_deadline.start(30000)
        return True

    def finish_reset(self,message):
        attempt=self.reset_request
        self.reset_request=None;self.reset_deadline.stop()
        outcome=(message.get('result') or {}).get('outcome')
        texts={'reset':'使用成功，正在同步新的额度和卡片数量。',
               'alreadyRedeemed':'这次请求已处理，正在同步最新数据。',
               'nothingToReset':'当前没有符合条件的额度可重置，本次未使用重置卡。',
               'noCredit':'没有可用的重置卡，正在刷新卡片信息。'}
        if outcome in ('nothingToReset','noCredit'):
            self.reset_keys.pop(attempt['credit_id'],None)
            if not self.owner.preview:atomic_json(STATE/'reset-attempts.json',self.reset_keys)
        self.owner.reset_refresh_pending=True
        self.owner.reset_result(texts.get(outcome,'使用结果未确认，请先刷新确认；重试同一张卡会复用原请求，避免重复使用。'))
        if self.pending is not None:self.refresh_after_reset=True
        else:self.refresh()

    def reset_timeout(self):
        if not self.reset_request:return
        self.reset_request=None
        self.owner.reset_refresh_pending=True
        self.owner.reset_result('请求超时，结果未确认。请先刷新；重试同一张卡会复用原请求。')
        if self.pending is not None:self.refresh_after_reset=True
        else:self.refresh()

    def timeout(self):
        self.pending = None
        self.owner.connection_error('连接超时，将自动重试。')
        if self.proc:
            self.proc.kill()

    def exited(self, *args):
        if self.reset_request:
            self.reset_request=None;self.reset_deadline.stop()
            self.owner.reset_refresh_pending=True
            self.owner.reset_result('连接中断，重置结果未确认；重新连接后请核对卡片数量。')
        self.ready = False
        self.pending = None
        if not self.stopping:
            self.owner.connection_error('额度连接已断开，将自动重试。')
            self.retry.start(15000)

    def stop(self):
        self.stopping = True
        self.retry.stop()
        self.deadline.stop()
        self.reset_deadline.stop()
        if self.proc and self.proc.state() != QProcess.NotRunning:
            self.proc.closeWriteChannel()
            if not self.proc.waitForFinished(1200):
                self.proc.kill()
                self.proc.waitForFinished(1200)


class RoundedMenu(QMenu):
    """Translucent popup with the same rounded surface as the card."""
    def __init__(self, card):
        super().__init__(card)
        self.card=card
        self.setWindowFlags(self.windowFlags()|Qt.FramelessWindowHint|Qt.NoDropShadowWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_StyledBackground,False)
        self.aboutToShow.connect(self.sync_style)
        self.sync_style()

    def sync_style(self):
        _,fg,muted,line=self.card.palette_values()
        item_radius=6
        selected=QColor(self.card.settings['accent'])
        self.setStyleSheet(f'''
            QMenu {{background:transparent;border:none;padding:9px;
                color:{fg.name()};font-family:"Microsoft YaHei UI";font-size:12px;}}
            QMenu::item {{background:transparent;padding:8px 24px;border-radius:{item_radius}px;}}
            QMenu::item:selected {{background:rgba({selected.red()},{selected.green()},{selected.blue()},35);}}
            QMenu::separator {{height:1px;background:{line.name()};margin:6px 10px;}}
        ''')

    def paintEvent(self,event):
        p=QPainter(self);p.setRenderHint(QPainter.Antialiasing)
        bg,fg,muted,line=self.card.palette_values()
        bg.setAlphaF(self.card.settings['opacity']/100)
        p.setBrush(bg);p.setPen(QPen(line,1))
        radius=10
        p.drawRoundedRect(QRectF(0.5,0.5,self.width()-1,self.height()-1),radius,radius)
        p.end()
        super().paintEvent(event)


class SettingsDialog(QDialog):
    def __init__(self, card):
        super().__init__(card)
        self.card = card
        self.setWindowTitle('额度卡片 · 个性化设置')
        self.setMinimumWidth(440)
        self.setStyleSheet('''
            QDialog {background:#242625;color:#f1f2ef;}
            QLabel,QCheckBox {color:#e2e6e1;font-family:"Microsoft YaHei UI";font-size:12px;}
            QComboBox {background:#343935;color:#edf3ed;border:1px solid #4c554d;border-radius:7px;padding:6px;}
            QComboBox QAbstractItemView {background:#343935;color:white;selection-background-color:#526b5c;}
            QSpinBox {background:#343935;color:#edf3ed;border:1px solid #4c554d;border-radius:7px;padding:6px;min-width:100px;}
            QPushButton {background:#343b36;color:#edf3ed;border:1px solid #4c554d;border-radius:8px;padding:8px 12px;}
            QPushButton:hover {background:#455449;}
            QSlider::groove:horizontal {background:#444c46;height:4px;border-radius:2px;}
            QSlider::handle:horizontal {background:#b5dcc7;width:14px;margin:-5px 0;border-radius:7px;}
        ''')
        layout = QVBoxLayout(self)
        layout.setContentsMargins(26,22,26,22)
        layout.setSpacing(14)
        heading = QLabel('让额度卡片，融入你的桌面')
        heading.setStyleSheet('font-size:17px;font-weight:600;color:#f3f6f1;')
        layout.addWidget(heading)
        caption = QLabel('调整即时预览，关闭此面板后自动保存。')
        caption.setStyleSheet('color:#a1aaa3;')
        layout.addWidget(caption)
        self.combo(layout,'配色主题','theme',list(PALETTES))
        self.slider(layout,'背景不透明度','opacity',20,100,'%')
        self.slider(layout,'圆角半径','radius',0,48,' px')
        self.slider(layout,'等比例缩放','scale',50,250,'%')
        self.slider(layout,'图片遮罩深度','image_shade',0,90,'%')
        images = QHBoxLayout()
        for text,slot in [('更换背景图片',card.choose_background),('恢复纯色',card.clear_background),('强调色',card.choose_accent)]:
            button=QPushButton(text);button.clicked.connect(slot);images.addWidget(button)
        layout.addLayout(images)
        checks=QHBoxLayout()
        for label,key in [('始终置顶','topmost'),('锁定位置','locked'),('紧凑模式','compact')]:
            box=QCheckBox(label);box.setChecked(card.settings[key]);box.toggled.connect(lambda v,k=key:card.set_option(k,v));checks.addWidget(box)
        layout.addLayout(checks)
        reset=QCheckBox('显示重置倒计时');reset.setChecked(card.settings['show_reset']);reset.toggled.connect(lambda v:card.set_option('show_reset',v));layout.addWidget(reset)
        refresh_row=QHBoxLayout();refresh_row.addWidget(QLabel('自动刷新（默认 3 秒）'));refresh_row.addStretch()
        refresh_spin=QSpinBox();refresh_spin.setRange(3,3600);refresh_spin.setSuffix(' 秒');refresh_spin.setValue(card.settings['interval'])
        refresh_spin.setKeyboardTracking(False)
        refresh_spin.valueChanged.connect(lambda v:card.set_option('interval',v))
        refresh_row.addWidget(refresh_spin);layout.addLayout(refresh_row)
        buckets=card.available_buckets or ['codex']
        self.combo(layout,'额度分组','bucket',buckets)
        self.startup = QCheckBox('登录 Windows 时自动启动（可随时取消）')
        self.startup.setChecked(card.settings['autostart'])
        self.startup.toggled.connect(card.toggle_autostart)
        layout.addWidget(self.startup)
        self.info=QLabel('数据来自本机 Codex 已登录账号。仅查询额度，不发送聊天。\n整张卡片等比例缩放，不拉伸；查询未完成时不会重复请求。')
        self.info.setWordWrap(True);self.info.setStyleSheet('font-size:11px;color:#9ca79f;');layout.addWidget(self.info)
        bottom=QHBoxLayout()
        reset_btn=QPushButton('恢复默认外观');reset_btn.clicked.connect(self.reset_appearance)
        done=QPushButton('完成');done.clicked.connect(self.accept)
        bottom.addWidget(reset_btn);bottom.addStretch();bottom.addWidget(done);layout.addLayout(bottom)

    def slider(self,layout,label,key,low,high,suffix):
        row=QHBoxLayout();title=QLabel(label);value=QLabel(str(self.card.settings[key])+suffix)
        row.addWidget(title);row.addStretch();row.addWidget(value);layout.addLayout(row)
        slider=QSlider(Qt.Horizontal);slider.setRange(low,high);slider.setValue(self.card.settings[key])
        slider.valueChanged.connect(lambda v:(value.setText(str(v)+suffix),self.card.set_option(key,v)))
        layout.addWidget(slider)

    def combo(self,layout,label,key,values,labels=None):
        row=QHBoxLayout();row.addWidget(QLabel(label));row.addStretch();combo=QComboBox()
        for value,text in zip(values,labels or [str(v) for v in values]):combo.addItem(text,value)
        i=combo.findData(self.card.settings[key]);combo.setCurrentIndex(max(0,i))
        combo.currentIndexChanged.connect(lambda _:self.card.set_option(key,combo.currentData()))
        row.addWidget(combo);layout.addLayout(row)

    def reset_appearance(self):
        for key in ('scale','radius','opacity','image','image_shade','accent','theme','compact','show_reset'):
            self.card.settings[key]=DEFAULTS[key]
        self.card.bg_image=QPixmap()
        self.card.apply_settings()
        self.accept()


class QuotaCard(QWidget):
    def __init__(self, preview=False):
        super().__init__()
        self.preview=preview
        self.settings=load_settings()
        self.payload={}
        self.last_success=None
        self.has_live_data=False
        self.status='尚未连接'
        self.error=False
        self.refreshing=False
        self.drag_start=None
        self.hover=''
        self.controls={}
        self.available_buckets=[]
        self.bg_image=QPixmap(self.settings['image']) if self.settings['image'] else QPixmap()
        self.dialog=None
        self.reset_dialog=None
        self.cards_synced_at=None
        self.reset_refresh_pending=False
        self.setWindowTitle('GPT 额度卡片')
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setMouseTracking(True)
        self.setWindowFlags(Qt.FramelessWindowHint|Qt.Tool)
        self.apply_settings(save=False)
        screen=QApplication.primaryScreen().availableGeometry()
        x=self.settings['x']
        x=screen.right()-self.width()-32 if x is None else x
        visible_rect=QRectF(x,self.settings['y'],self.width(),self.height())
        if not any(visible_rect.intersects(QRectF(s.availableGeometry())) for s in QApplication.screens()):
            x=screen.right()-self.width()-32;self.settings['y']=90
        self.move(int(x),int(self.settings['y']))
        self.create_tray()
        self.timer=QTimer(self);self.timer.timeout.connect(self.tick);self.timer.start(1000)
        self.poll=QTimer(self);self.poll.timeout.connect(self.refresh);self.poll.start(self.settings['interval']*1000)
        self.client=UsageClient(self)
        if not preview:
            try:
                cache=json.loads((STATE/'usage-cache.json').read_text('utf-8'))
                self.payload=cache['payload'];self.last_success=cache['timestamp']
                self.status='正在重新连接 · 显示上次数据';self.error=True
            except (OSError,ValueError,KeyError):pass
            self.save()
            QTimer.singleShot(300,self.client.start)

    def apply_settings(self, save=True):
        scale=self.settings['scale']/100
        self.setFixedSize(round(360*scale),round((210 if self.settings['compact'] else 278)*scale))
        old_visibility=self.isVisible()
        flags=Qt.FramelessWindowHint|Qt.Tool
        if self.settings['topmost']:flags|=Qt.WindowStaysOnTopHint
        if self.windowFlags()!=flags:
            self.setWindowFlags(flags)
            if old_visibility:self.show()
        if hasattr(self,'poll'):self.poll.setInterval(int(self.settings['interval'])*1000)
        self.update()
        if save:self.save()

    def save(self):
        if self.preview:return
        self.settings.update(x=self.x(),y=self.y())
        atomic_json(STATE/'settings.json',self.settings)

    def set_option(self,key,value):
        self.settings[key]=value
        self.apply_settings()
        if key=='bucket':self.refresh()

    def create_tray(self):
        pix=QPixmap(64,64);pix.fill(Qt.transparent)
        p=QPainter(pix);p.setRenderHint(QPainter.Antialiasing)
        p.setBrush(QColor('#292e2a'));p.setPen(Qt.NoPen);p.drawRoundedRect(1,1,62,62,17,17)
        p.setPen(QPen(QColor('#a4d8c1'),5,Qt.SolidLine,Qt.RoundCap));p.drawArc(15,15,34,34,0,270*16)
        p.drawLine(32,32,43,21);p.end()
        self.setWindowIcon(QIcon(pix))
        self.tray=QSystemTrayIcon(QIcon(pix),self)
        self.tray.setToolTip('GPT 额度卡片 · Codex')
        self.tray.activated.connect(lambda reason:self.restore() if reason in (QSystemTrayIcon.Trigger,QSystemTrayIcon.DoubleClick) else None)
        self.tray_menu=RoundedMenu(self)
        self.tray_menu.addAction('显示卡片',self.restore)
        self.tray_menu.addAction('隐藏卡片',self.hide)
        self.tray_menu.addAction('刷新额度',self.refresh)
        self.tray_menu.addAction('个性化设置',self.open_settings)
        self.tray_menu.addSeparator();self.tray_menu.addAction('退出',self.close)
        self.tray.setContextMenu(self.tray_menu)
        if not self.preview:self.tray.show()

    def restore(self):
        self.show();self.raise_()

    def connection_status(self,text):
        self.refreshing=True;self.status=text;self.update()

    def connection_error(self,text):
        self.refreshing=False;self.error=True;self.status=text
        self.setToolTip(text);self.update()

    def accept_usage(self,payload,partial=False):
        if partial:
            payload={**self.payload,**payload}
        else:
            self.cards_synced_at=time.time()
            self.reset_refresh_pending=False
        bucket,self.available_buckets=extract_bucket(payload,self.settings['bucket'])
        if not bucket:
            self.connection_error('当前账号未返回额度数据。请确认使用 ChatGPT 登录。');return
        self.payload=payload;self.last_success=time.time();self.has_live_data=True;self.error=False;self.refreshing=False
        self.status='已连接 · 自动刷新';self.setToolTip('拖动移动 · 双击打开设置 · 右键查看更多操作')
        if not self.preview:atomic_json(STATE/'usage-cache.json',dict(payload=payload,timestamp=self.last_success))
        self.update()
        if self.reset_dialog:self.reset_dialog.sync()

    def reset_data_fresh(self):
        return (self.has_live_data and not self.error and self.client.ready
                and not self.reset_refresh_pending and self.cards_synced_at is not None
                and time.time()-self.cards_synced_at<max(60,self.settings['interval']*2))

    def open_reset_cards(self):
        if self.reset_dialog:
            self.reset_dialog.show();self.reset_dialog.raise_();return
        self.reset_dialog=ResetCardsDialog(self)
        self.reset_dialog.finished.connect(self.reset_dialog_closed)
        self.reset_dialog.show()
        self.refresh()

    def reset_dialog_closed(self):
        self.reset_dialog.deleteLater();self.reset_dialog=None

    def reset_result(self,text):
        if self.reset_dialog:self.reset_dialog.show_result(text)
        else:self.tray.showMessage('额度重置卡',text,QSystemTrayIcon.Information,7000)

    def refresh(self):
        if not self.preview:self.client.refresh()

    def tick(self):
        self.update()

    def palette_values(self):
        bg,fg,muted,line=PALETTES[self.settings['theme']]
        return QColor(bg),QColor(fg),QColor(muted),QColor(line)

    def draw_text(self,p,x,y,w,h,text,size=10,color='#fff',bold=False,align=Qt.AlignLeft|Qt.AlignVCenter):
        font=QFont('Microsoft YaHei UI',size);font.setWeight(QFont.DemiBold if bold else QFont.Normal)
        p.setFont(font);p.setPen(QColor(color))
        p.drawText(QRectF(x,y,w,h),align,str(text))

    def icon(self,p,name,rect,color):
        p.save();p.setPen(QPen(color,1.4,Qt.SolidLine,Qt.RoundCap,Qt.RoundJoin));p.setBrush(Qt.NoBrush)
        cx,cy=rect.center().x(),rect.center().y()
        if name=='refresh':
            p.drawArc(QRectF(cx-6,cy-6,12,12),30*16,290*16)
            p.drawLine(QPoint(int(cx+6),int(cy-7)),QPoint(int(cx+6),int(cy-2)));p.drawLine(QPoint(int(cx+6),int(cy-2)),QPoint(int(cx+1),int(cy-2)))
        elif name=='settings':
            p.drawEllipse(QRectF(cx-3,cy-3,6,6));p.drawEllipse(QRectF(cx-7,cy-7,14,14))
            for a in range(0,360,45):
                x,y=math.cos(math.radians(a)),math.sin(math.radians(a));p.drawLine(QPoint(int(cx+7*x),int(cy+7*y)),QPoint(int(cx+9*x),int(cy+9*y)))
        elif name=='pin':
            p.drawLine(QPoint(int(cx),int(cy+2)),QPoint(int(cx),int(cy+8)))
            p.drawRect(QRectF(cx-4,cy-6,8,5));p.drawLine(QPoint(int(cx-6),int(cy+2)),QPoint(int(cx+6),int(cy+2)))
        elif name=='menu':
            p.setBrush(color);p.setPen(Qt.NoPen)
            for dx in [-5,0,5]:p.drawEllipse(QRectF(cx+dx-1,cy-1,2,2))
        p.restore()

    def paintEvent(self,event):
        p=QPainter(self);p.setRenderHint(QPainter.Antialiasing);p.setRenderHint(QPainter.SmoothPixmapTransform)
        p.scale(self.settings['scale']/100,self.settings['scale']/100)
        bg,fg,muted,line=self.palette_values();w,h=360,210 if self.settings['compact'] else 278
        rect=QRectF(1,1,w-2,h-2);radius=self.settings['radius']
        clip=QPainterPath();clip.addRoundedRect(rect,radius,radius)
        p.setClipPath(clip)
        bg.setAlphaF(self.settings['opacity']/100);p.fillPath(clip,bg)
        if not self.bg_image.isNull():
            p.save();p.setOpacity(self.settings['opacity']/100)
            scaled=self.bg_image.scaled(w,h,Qt.KeepAspectRatioByExpanding,Qt.SmoothTransformation)
            p.drawPixmap((w-scaled.width())//2,(h-scaled.height())//2,scaled)
            shade=QColor('#101713');shade.setAlphaF(self.settings['image_shade']/100);p.fillRect(rect,shade);p.restore()
            fg,muted,line=QColor('#f6f8f5'),QColor('#c0c9c2'),QColor('#ffffff')
            line.setAlpha(45)
        p.setClipping(False)
        accent=QColor(self.settings['accent']);bucket,_=extract_bucket(self.payload,self.settings['bucket'])
        settings_y=h-35 if self.settings['compact'] else h-43
        self.controls={'pin':QRectF(w-96,16,24,26),'refresh':QRectF(w-66,16,24,26),'menu':QRectF(w-36,16,24,26),'settings':QRectF(w-42,settings_y,26,27),'reset_cards':QRectF(18,settings_y,210,27)}
        # Gauge emblem deliberately follows the reference's fine strokes.
        p.setPen(QPen(accent,1.7,Qt.SolidLine,Qt.RoundCap));p.drawArc(QRectF(23,23,17,17),-25*16,230*16)
        p.drawLine(QPoint(31,32),QPoint(38,24));p.drawEllipse(QRectF(29,30,3,3))
        self.draw_text(p,49,16,w-165,29,'剩余额度',12,fg,True)
        for name,area in self.controls.items():
            if self.hover==name:
                hover=QColor(fg);hover.setAlpha(16)
                p.setPen(Qt.NoPen);p.setBrush(hover)
                button_radius=min(radius,area.width()/2,area.height()/2)
                p.drawRoundedRect(area,button_radius,button_radius)
            if name in ('settings','reset_cards'):continue
            col=accent if name=='pin' and self.settings['topmost'] else muted
            self.icon(p,name,area,col)
        compact=self.settings['compact']
        if not compact:
            plan=(bucket or {}).get('planType')
            plan={'team':'BUSINESS','business':'BUSINESS','plus':'PLUS','pro':'PRO','free':'FREE','enterprise':'ENTERPRISE'}.get(plan,(plan or 'ACCOUNT').upper())
            self.draw_text(p,24,48,w-48,20,f"CODEX  /  {plan}",8,muted)
        separator_y=56 if compact else 78
        p.setPen(QPen(line,1));p.drawLine(24,separator_y,w-24,separator_y)
        start=68 if compact else 92
        for index,key in enumerate(('primary','secondary')):
            window=(bucket or {}).get(key)
            y=start+index*(56 if compact else 71)
            val=remaining(window)
            number='—' if val is None else f'{val:g}'
            low_quota=val is not None and val<20
            color=QColor('#f06464') if low_quota else accent
            self.draw_text(p,24,y,w-145,22,duration_label(window,['5 小时额度','每周额度'][index]),10,fg,True)
            self.draw_text(p,w-118,y-7,72,32,number,21,color,True,Qt.AlignRight|Qt.AlignVCenter)
            self.draw_text(p,w-44,y+1,22,20,'%' if val is not None else '',10,color if low_quota else muted)
            if self.settings['show_reset']:
                self.draw_text(p,24,y+23,w-48,17,countdown(window.get('resetsAt') if window else None),8,muted)
            track_y=y+(44 if not compact else 43)
            track=QColor(line);track.setAlpha(150)
            p.setPen(Qt.NoPen);p.setBrush(track);p.drawRoundedRect(QRectF(24,track_y,w-48,3),1.5,1.5)
            if val is not None and val>0:
                p.setBrush(color);p.drawRoundedRect(QRectF(24,track_y,(w-48)*val/100,3),1.5,1.5)
        count,_=reset_inventory(self.payload)
        label=f'剩余 {count if count is not None else "—"} 张重置卡'
        self.draw_text(p,24,settings_y,195,27,label,9,muted)
        self.icon(p,'settings',self.controls['settings'],muted)
        p.end()
        if bucket:
            a,b=remaining(bucket.get('primary')),remaining(bucket.get('secondary'))
            self.tray.setToolTip(f'Codex 剩余 · 5小时 {a if a is not None else "—"}% · 每周 {b if b is not None else "—"}%')

    def control_at(self,pos):
        scale=self.settings['scale']/100
        logical_pos=QPointF(pos.x()/scale,pos.y()/scale)
        return next((name for name,rect in self.controls.items() if rect.contains(logical_pos)), '')

    def mousePressEvent(self,event):
        if event.button()!=Qt.LeftButton:return
        control=self.control_at(event.pos())
        if control=='pin':self.set_option('topmost',not self.settings['topmost'])
        elif control=='refresh':self.refresh()
        elif control=='settings':self.open_settings()
        elif control=='reset_cards':self.open_reset_cards()
        elif control=='menu':self.open_menu(event.globalPos())
        elif not self.settings['locked']:self.drag_start=event.globalPos()-self.frameGeometry().topLeft()

    def mouseMoveEvent(self,event):
        hover=self.control_at(event.pos())
        if hover!=self.hover:self.hover=hover;self.update()
        self.setCursor(Qt.PointingHandCursor if hover else Qt.ArrowCursor)
        if self.drag_start is not None and event.buttons()&Qt.LeftButton:
            target=event.globalPos()-self.drag_start
            # Snap each edge to its current monitor without forcing a primary monitor.
            screen=QApplication.screenAt(event.globalPos()) or QApplication.primaryScreen();a=screen.availableGeometry()
            x,y=target.x(),target.y()
            for snap in (a.left()+12,a.right()-self.width()-12):
                if abs(x-snap)<12:x=snap
            for snap in (a.top()+12,a.bottom()-self.height()-12):
                if abs(y-snap)<12:y=snap
            self.move(x,y)

    def mouseReleaseEvent(self,event):
        if self.drag_start is not None:self.drag_start=None;self.save()

    def leaveEvent(self,event):
        self.hover='';self.update()
        super().leaveEvent(event)

    def mouseDoubleClickEvent(self,event):
        if not self.control_at(event.pos()):self.open_settings()

    def contextMenuEvent(self,event):self.open_menu(event.globalPos())

    def open_menu(self,point):
        menu=RoundedMenu(self)
        for text,key in [('锁定位置','locked'),('紧凑模式','compact')]:
            action=menu.addAction(text);action.setCheckable(True);action.setChecked(self.settings[key]);action.toggled.connect(lambda v,k=key:self.set_option(k,v))
        menu.addSeparator();menu.addAction('更换背景图片…',self.choose_background);menu.addAction('外观与个性化…',self.open_settings)
        menu.addAction('隐藏到托盘',self.hide)
        menu.addSeparator();menu.addAction('退出卡片',self.close)
        menu.exec_(point)
        self.hover='';self.update()
        menu.deleteLater()

    def choose_background(self):
        file,_=QFileDialog.getOpenFileName(self,'选择卡片背景图片','','图片 (*.png *.jpg *.jpeg *.webp *.bmp)')
        if file:
            pix=QPixmap(file)
            if pix.isNull():QMessageBox.warning(self,'无法读取','请选择有效的图片文件。');return
            self.bg_image=pix;self.set_option('image',file)

    def clear_background(self):
        self.bg_image=QPixmap();self.set_option('image','')

    def choose_accent(self):
        color=QColorDialog.getColor(QColor(self.settings['accent']),self,'选择强调色')
        if color.isValid():self.set_option('accent',color.name())

    def open_settings(self):
        if self.dialog:
            self.dialog.show();self.dialog.raise_();self.dialog.activateWindow();return
        self.dialog=SettingsDialog(self)
        self.dialog.finished.connect(self.dialog_closed)
        self.dialog.show()

    def dialog_closed(self):
        self.save();self.dialog.deleteLater();self.dialog=None

    def toggle_autostart(self,enabled):
        try:
            startup=Path(os.environ['APPDATA'])/'Microsoft/Windows/Start Menu/Programs/Startup/GPT Quota Card.lnk'
            if enabled:
                pythonw=Path(sys.executable).with_name('pythonw.exe')
                def q(s):return "'"+str(s).replace("'","''")+"'"
                command=(f'$s=(New-Object -ComObject WScript.Shell).CreateShortcut({q(startup)});'
                    f'$s.TargetPath={q(pythonw)};$s.Arguments={q(chr(34)+str(BASE/"quota_card.py")+chr(34))};'
                    f'$s.WorkingDirectory={q(BASE)};$s.Description="GPT Quota Card";$s.Save()')
                subprocess.run(['powershell.exe','-NoProfile','-Command',command],check=True,
                    creationflags=subprocess.CREATE_NO_WINDOW,capture_output=True)
            else:
                startup.unlink(missing_ok=True)
            self.set_option('autostart',enabled)
        except (OSError,subprocess.SubprocessError):
            QMessageBox.warning(self,'开机启动设置失败','无法修改启动快捷方式，请检查当前用户权限。')
            if self.dialog:
                self.dialog.startup.blockSignals(True);self.dialog.startup.setChecked(self.settings['autostart']);self.dialog.startup.blockSignals(False)

    def closeEvent(self,event):
        self.save();self.poll.stop();self.client.stop();self.tray.hide()
        event.accept();QApplication.quit()


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--preview');parser.add_argument('--settings-preview');parser.add_argument('--probe',action='store_true');parser.add_argument('--snapshot')
    args=parser.parse_args()
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps)
    app=QApplication(sys.argv);app.setQuitOnLastWindowClosed(False)
    app.setApplicationName('GPT Quota Card')
    STATE.mkdir(exist_ok=True)
    lock=None
    if not args.preview and not args.settings_preview and not args.probe:
        lock=QLockFile(str(STATE/'instance.lock'));lock.setStaleLockTime(30000)
        if not lock.tryLock(100):
            QMessageBox.information(None,'额度卡片已运行','卡片已经在运行，请点击右下角系统托盘的额度卡片图标。');return
    card=QuotaCard(preview=bool(args.preview or args.settings_preview))
    card.show()
    if args.preview or args.settings_preview:
        # Explicit design preview fixture; never used by the live widget.
        card.accept_usage({'rateLimitsByLimitId':{'codex':dict(planType='team',primary=dict(usedPercent=12,windowDurationMins=300,resetsAt=time.time()+10620),secondary=dict(usedPercent=16,windowDurationMins=10080,resetsAt=time.time()+5*86400+6300))}})
        def capture():
            if args.settings_preview:
                card.open_settings();QTimer.singleShot(500,lambda:(card.dialog.grab().save(args.settings_preview),app.quit()))
            else:card.grab().save(args.preview);app.quit()
        QTimer.singleShot(500,capture)
    elif args.probe:
        def result_exit():
            if card.has_live_data:
                data,_=extract_bucket(card.payload)
                count,_=reset_inventory(card.payload)
                print(json.dumps({'connected':True,'primaryRemaining':remaining(data.get('primary')),'secondaryRemaining':remaining(data.get('secondary')),'resetCardsAvailable':count}))
                if args.snapshot:card.grab().save(args.snapshot)
                card.close()
        card.timer.timeout.connect(result_exit)
        QTimer.singleShot(45000,lambda:(print('PROBE_TIMEOUT'),card.close()))
    app.aboutToQuit.connect(card.client.stop)
    app.exec_()
    if lock:lock.unlock()


if __name__=='__main__':
    try:main()
    except Exception:
        import traceback
        STATE.mkdir(exist_ok=True)
        (STATE/'error.log').write_text(traceback.format_exc(),'utf-8')
        raise
