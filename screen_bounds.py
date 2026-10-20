"""Screen geometry in Qt logical pixels (also works on negative-origin monitors)."""
import math
from PyQt5.QtCore import QPoint


def bounded_position(point, size, area, snap=0):
    left,top=area.x(),area.y()
    right=max(left,left+area.width()-size.width())
    bottom=max(top,top+area.height()-size.height())
    x=max(left,min(point.x(),right));y=max(top,min(point.y(),bottom))
    if snap:
        if x-left<snap:x=left
        elif right-x<snap:x=right
        if y-top<snap:y=top
        elif bottom-y<snap:y=bottom
    return QPoint(x,y)


def fitting_scale(requested, logical_width, logical_height, area):
    limit=math.floor(min(area.width()/logical_width,area.height()/logical_height)*100)
    return max(1,min(requested,limit))


def screen_index(rect, areas):
    """Prefer greatest overlap; recover off-screen windows onto nearest monitor."""
    center=rect.center()
    def score(area):
        intersection=rect.intersected(area)
        overlap=max(0,intersection.width())*max(0,intersection.height())
        dx=max(area.left()-center.x(),0,center.x()-area.right())
        dy=max(area.top()-center.y(),0,center.y()-area.bottom())
        return overlap,-(dx*dx+dy*dy)
    return max(range(len(areas)),key=lambda i:score(areas[i]))
