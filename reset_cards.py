"""Reset-card presentation and validation; no network or redemption on its own."""
from datetime import datetime
import time

from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtWidgets import (QDialog,QVBoxLayout,QHBoxLayout,QLabel,QPushButton,
    QScrollArea,QWidget,QFrame,QMessageBox)


def reset_inventory(payload):
    inventory=payload.get('rateLimitResetCredits')
    if not isinstance(inventory,dict):return None,None
    count=inventory.get('availableCount')
    if isinstance(count,bool) or not isinstance(count,int) or count<0:count=None
    cards=inventory.get('credits')
    return count,[c for c in cards if isinstance(c,dict)] if isinstance(cards,list) else None


def reset_usable(card,now=None):
    if card.get('status')!='available' or card.get('resetType')!='codexRateLimits':return False
    if not isinstance(card.get('id'),str) or not card['id'].strip():return False
    expires=card.get('expiresAt')
    if expires is None:return True
    try:return float(expires)>(time.time() if now is None else now)
    except (ValueError,TypeError):return False


def card_title(card):
    title=card.get('title')
    return {'Full reset (Weekly + 5 hr)':'完整重置（每周 + 5 小时）',
            'Rate-limit reset':'额度重置卡'}.get(title,title or '额度重置卡')


def local_date(timestamp,unknown):
    if timestamp is None:return unknown
    try:return datetime.fromtimestamp(float(timestamp)).strftime('%Y-%m-%d %H:%M')
    except (ValueError,TypeError,OverflowError,OSError):return unknown


class ResetCardsDialog(QDialog):
    def __init__(self,card):
        super().__init__(card)
        self.card=card
        self.signature=None
        self.buttons=[]
        self.waiting_notice=False
        self.setWindowTitle('额度重置卡')
        self.resize(490,550)
        self.setMinimumSize(440,420)
        self.setStyleSheet('''
            QDialog {background:#242625;}
            QLabel {color:#edf2ec;font-family:"Microsoft YaHei UI";font-size:12px;}
            QPushButton {background:#37443b;color:#e3f3e9;border:1px solid #526759;
                border-radius:8px;padding:8px 14px;}
            QPushButton:hover {background:#455b4c;}
            QPushButton:disabled {background:#303431;color:#7e8980;border-color:#3c453e;}
            QScrollArea {background:transparent;border:none;}
            QScrollBar:vertical {background:#242a25;width:9px;border:none;margin:0;}
            QScrollBar::handle:vertical {background:#59695d;border-radius:4px;min-height:30px;}
            QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical {height:0;}
            QScrollBar::add-page:vertical,QScrollBar::sub-page:vertical {background:none;}
            QWidget#resetList {background:transparent;}
            QFrame#resetItem {background:#2f3430;border:1px solid #434e45;border-radius:10px;}
            QFrame#resetItem QLabel {border:none;background:transparent;}
        ''')
        layout=QVBoxLayout(self);layout.setContentsMargins(22,22,22,20);layout.setSpacing(14)
        self.heading=QLabel('额度重置卡');self.heading.setStyleSheet('font-size:18px;font-weight:600;')
        layout.addWidget(self.heading)
        hint=QLabel('使用前请确认所选卡片。重置范围以该卡说明和服务端结果为准。')
        hint.setWordWrap(True);hint.setStyleSheet('color:#aebbb1;');layout.addWidget(hint)
        self.notice=QLabel('');self.notice.setTextFormat(Qt.PlainText);self.notice.setWordWrap(True)
        self.notice.setStyleSheet('color:#c7d9ca;');layout.addWidget(self.notice)
        scroll=QScrollArea();scroll.setWidgetResizable(True)
        self.list=QWidget();self.list.setObjectName('resetList');self.rows=QVBoxLayout(self.list)
        self.rows.setContentsMargins(0,0,4,0);self.rows.setSpacing(10);self.rows.addStretch()
        scroll.setWidget(self.list);layout.addWidget(scroll,1)
        bottom=QHBoxLayout();refresh=QPushButton('刷新卡片');refresh.clicked.connect(card.refresh)
        bottom.addWidget(refresh);bottom.addStretch();close=QPushButton('关闭');close.clicked.connect(self.close);bottom.addWidget(close)
        layout.addLayout(bottom)
        self.timer=QTimer(self);self.timer.timeout.connect(self.sync);self.timer.start(1000)
        self.sync()

    def label(self,text):
        label=QLabel(str(text));label.setTextFormat(Qt.PlainText);label.setWordWrap(True);return label

    def sync(self):
        count,cards=reset_inventory(self.card.payload)
        self.heading.setText(f'剩余 {count if count is not None else "—"} 张重置卡')
        fresh=self.card.reset_data_fresh()
        if not fresh and not self.card.client.reset_request and not self.card.reset_refresh_pending:
            self.notice.setText('正在确认最新卡片数据；离线或过期缓存不能用于重置。')
            self.waiting_notice=True
        elif fresh and self.waiting_notice:
            self.notice.clear();self.waiting_notice=False
        signature=repr((count,cards))
        if signature!=self.signature:
            self.signature=signature
            while self.rows.count()>1:
                item=self.rows.takeAt(0)
                if item.widget():item.widget().deleteLater()
            self.buttons=[]
            if cards is None:
                message='服务暂未提供卡片详情，请刷新后重试。' if count else '暂未获取到卡片信息，请确认连接后刷新。'
                self.rows.insertWidget(0,self.label(message))
            elif not cards:
                self.rows.insertWidget(0,self.label('暂无可用重置卡。' if not count else '服务尚未返回可选择的卡片详情，请刷新。'))
            else:
                for index,credit in enumerate(cards):
                    frame=QFrame();frame.setObjectName('resetItem');box=QVBoxLayout(frame);box.setContentsMargins(14,12,14,12)
                    title=self.label(card_title(credit));title.setStyleSheet('font-size:13px;font-weight:600;');box.addWidget(title)
                    box.addWidget(self.label(credit.get('description') or '暂无额外说明。'))
                    box.addWidget(self.label('获得时间：'+local_date(credit.get('grantedAt'),'未知')))
                    box.addWidget(self.label('到期时间：'+local_date(credit.get('expiresAt'),'未提供到期时间')))
                    state={'available':'可用','redeemed':'已使用','consumed':'已使用','expired':'已过期'}.get(credit.get('status'),credit.get('status') or '状态未知')
                    box.addWidget(self.label('状态：'+state+' · 类型：'+str(credit.get('resetType') or '未知')))
                    row=QHBoxLayout();row.addStretch();button=QPushButton('使用这张卡')
                    button.clicked.connect(lambda _,c=dict(credit):self.confirm_use(c));row.addWidget(button);box.addLayout(row)
                    self.buttons.append((button,credit));self.rows.insertWidget(index,frame)
                if count is not None and count>len(cards):
                    self.rows.insertWidget(len(cards),self.label('当前仅显示服务返回的部分详情，总可用张数以顶部数量为准。'))
        busy=self.card.client.reset_request is not None or self.card.reset_refresh_pending
        for button,credit in self.buttons:
            button.setEnabled(fresh and not busy and count is not None and count>0 and reset_usable(credit))
            button.setText('处理中…' if busy else '使用这张卡')

    def confirm_use(self,credit):
        if not self.card.reset_data_fresh() or not reset_usable(credit):
            self.notice.setText('卡片信息已过期或尚未同步，请刷新后重试。');return
        text=(f'确定使用“{card_title(credit)}”吗？\n\n'
              '将消耗这张重置卡，重置符合条件的 Codex 额度窗口。\n'
              '此操作不能撤回，实际重置范围以服务端结果为准。')
        answer=QMessageBox.question(self,'确认使用重置卡',text,QMessageBox.Yes|QMessageBox.No,QMessageBox.No)
        if answer==QMessageBox.Yes:
            # Revalidate after the modal confirmation; background updates may have arrived.
            count,cards=reset_inventory(self.card.payload)
            current=next((c for c in cards or [] if c.get('id')==credit.get('id')),None)
            if not self.card.reset_data_fresh() or not current or not count or not reset_usable(current):
                self.notice.setText('卡片状态已变化，请刷新后重新选择。');return
            if self.card.client.consume_reset(current):self.notice.setText('正在使用重置卡，请勿重复操作…')
            self.sync()

    def show_result(self,text):
        self.waiting_notice=False;self.notice.setText(text);self.sync()

    def closeEvent(self,event):
        self.timer.stop();super().closeEvent(event)
