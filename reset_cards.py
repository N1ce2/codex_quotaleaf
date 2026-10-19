"""Reset-card validation and compact popup menus; never opens a detail window."""
from datetime import datetime
import time


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


def confirm_selected_reset(owner,credit):
    """Invoked only by the explicit confirmation action in a card's submenu."""
    count,cards=reset_inventory(owner.payload)
    current=next((c for c in cards or [] if c.get('id')==credit.get('id')),None)
    if not owner.reset_data_fresh() or not count or not current or not reset_usable(current):
        owner.reset_result('卡片状态已变化，请刷新后重新选择。')
        return False
    return owner.client.consume_reset(current)


def sync_reset_menu(menu,owner):
    count,cards=reset_inventory(owner.payload)
    menu.inventory_heading.setText(f'剩余 {count if count is not None else "—"} 张重置卡')
    fresh=owner.reset_data_fresh()
    busy=owner.client.reset_request is not None or owner.reset_refresh_pending
    current={c.get('id'):c for c in cards or []}
    for credit,action,confirm in menu.credit_actions:
        latest=current.get(credit.get('id'))
        usable=bool(fresh and not busy and count and latest and reset_usable(latest))
        action.setEnabled(usable);confirm.setEnabled(usable)
    message=owner.reset_message
    if busy:message='正在处理，请勿重复使用…'
    elif not fresh:message='连接待同步，暂时无法使用'
    menu.status_action.setText(message)
    menu.status_action.setVisible(bool(message))


def populate_reset_menu(menu,owner,menu_factory):
    """Each card gets a short confirmation submenu instead of a QDialog."""
    count,cards=reset_inventory(owner.payload)
    menu.credit_actions=[]
    menu.inventory_heading=menu.addAction('');menu.inventory_heading.setEnabled(False)
    menu.status_action=menu.addAction('');menu.status_action.setEnabled(False)
    menu.addSeparator()
    if not cards:
        text='暂无可用重置卡' if count==0 else '暂未返回卡片详情，请稍后刷新'
        menu.addAction(text).setEnabled(False)
    for index,credit in enumerate(cards or [],1):
        title=card_title(credit)
        short='完整重置' if credit.get('title')=='Full reset (Weekly + 5 hr)' else title
        if len(short)>22:short=short[:21]+'…'
        expires=local_date(credit.get('expiresAt'),'')
        expiry=(expires[5:10]+' 到期') if expires else '到期时间未知'
        child=menu_factory(owner)
        child.setParent(menu,child.windowFlags())
        child.setTitle(f'{index}. {short} · {expiry}')
        child.addAction(title).setEnabled(False)
        child.addAction('将消耗 1 张重置卡，使用后不可撤回').setEnabled(False)
        child.addSeparator()
        confirm=child.addAction('确认使用此卡')
        confirm.triggered.connect(lambda _,c=dict(credit):confirm_selected_reset(owner,c))
        child.addAction('取消',menu.close)
        action=menu.addMenu(child)
        menu.credit_actions.append((dict(credit),action,confirm))
    if cards and count is not None and count>len(cards):
        menu.addAction('仅显示服务返回的部分卡片').setEnabled(False)
    sync_reset_menu(menu,owner)
