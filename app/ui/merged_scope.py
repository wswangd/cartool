"""合并测量信号：同单位叠加，两种单位双纵轴，更多单位分栏，共用时间窗口。"""
from bisect import bisect_right
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPen, QPolygonF
from .signal_scope import SignalScope


class MergedScope(SignalScope):
    COLORS = ('#0078D4', '#D55E00', '#009E73', '#9C42A5', '#A66F00', '#318C91')

    def __init__(self, cards, parent=None):
        super().__init__(parent)
        self.cards = cards
        self.hidden_cards = set()
        self.setMinimumHeight(150)

    def _cursor_plot(self):
        units = {card.unit for card in self.cards if id(card) not in self.hidden_cards}
        return QRectF(55,20,max(1,self.width()-(114 if len(units)==2 else 67)),max(1,self.height()-44))

    def _time_label_at(self, position):
        units = {card.unit for card in self.cards if id(card) not in self.hidden_cards}
        count = 1 if len(units)==2 else max(1,len(units))
        gap = 28
        lane_height = max(1,(self.height()-24-gap*(count-1))/count)
        for lane in range(count):
            bottom = lane*(lane_height+gap)+20+max(1,lane_height-30)
            rect = QRectF(33,bottom+4,44,18)
            if rect.contains(position):
                return rect
        return None

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.fillRect(self.rect(), QColor('#F7F8F6'))
        groups = {}
        for i, card in enumerate(self.cards):
            if id(card) in self.hidden_cards:
                continue
            groups.setdefault(card.unit, []).append((i, card))
        dual_axis = len(groups) == 2
        lane_count = 1 if dual_axis else len(groups)
        all_labels = []
        gap = 28
        lane_height = max(1, (self.height()-24-gap*max(0,lane_count-1))/max(1,lane_count))
        for lane, (unit, members) in enumerate(groups.items()):
            top = 0 if dual_axis else lane*(lane_height+gap)
            plot = QRectF(55,top+20,max(1,self.width()-(114 if dual_axis else 67)),max(1,lane_height-30))
            hover_x = self._cursor_x(plot)
            values = [v for _,card in members if card.scope for t,v in card.scope.samples]
            low,high = (min(values),max(values)) if values else (0,1)
            margin = (high-low)*.1 or max(abs(low)*.1,1)
            low,high = low-margin,high+margin
            painter.setPen(QColor('#687568'))
            painter.drawText(QRectF(plot.left(),top,plot.width(),18),Qt.AlignRight if dual_axis and lane == 1 else Qt.AlignLeft,unit or '无单位')
            axis_x = plot.right() if dual_axis and lane == 1 else plot.left()
            painter.drawLine(QPointF(axis_x,plot.top()),QPointF(axis_x,plot.bottom()))
            for j in range(5):
                y = plot.top()+plot.height()*j/4
                painter.setPen(QPen(QColor('#BFC6BD'),1,Qt.DotLine))
                if not dual_axis or lane == 0:
                    painter.drawLine(QPointF(plot.left(),y),QPointF(plot.right(),y))
                painter.setPen(QColor('#687568'))
                painter.drawText(QRectF(plot.right()+6 if dual_axis and lane == 1 else 0,y-8,49,16),Qt.AlignLeft if dual_axis and lane == 1 else Qt.AlignRight,format(high-(high-low)*j/4,'.4g'))
            for j in range(21):
                x = plot.left()+plot.width()*j/20
                painter.setPen(QColor('#BFC6BD'))
                painter.drawLine(QPointF(x,plot.bottom()),QPointF(x,plot.bottom()+3))
            end = self._view_time or 0
            start = end-self.WINDOW_SECONDS
            labels = all_labels if dual_axis else []
            for i,card in members:
                if card.scope is None:
                    continue
                points = QPolygonF()
                for timestamp,value in card.scope.samples:
                    point = QPointF(plot.left()+(timestamp-start)/self.WINDOW_SECONDS*plot.width(),
                                    plot.bottom()-(value-low)/(high-low)*plot.height())
                    if points:
                        points.append(QPointF(point.x(),points[-1].y()))
                    points.append(point)
                painter.save()
                painter.setClipRect(plot)
                painter.setPen(QPen(QColor(self.COLORS[i%len(self.COLORS)]),2))
                painter.drawPolyline(points)
                painter.restore()
                samples = card.scope.samples
                if not samples:
                    continue
                sample = samples[-1]
                cursor = plot.left()+(sample[0]-start)/self.WINDOW_SECONDS*plot.width()
                if hover_x is not None and plot.left()<=hover_x<=plot.right():
                    timestamp = start+(hover_x-plot.left())/plot.width()*self.WINDOW_SECONDS
                    timestamps = [t for t,v in samples]
                    index = bisect_right(timestamps,timestamp)-1
                    if index < 0 or timestamp > timestamps[-1]:
                        continue
                    sample = samples[index]
                    cursor = hover_x
                if not plot.left()<=cursor<=plot.right():
                    continue
                text = f"{sample[1]:.10g}"
                label_width = min(plot.width(),painter.fontMetrics().horizontalAdvance(text)+14)
                value_y = plot.bottom()-(sample[1]-low)/(high-low)*plot.height()
                label = QRectF(max(plot.left(),min(cursor+8,plot.right()-label_width)),
                               max(plot.top(),min(value_y-28,plot.bottom()-24)),label_width,24)
                # 延后绘制提示，后续信号曲线不能覆盖已绘制的数值框。
                for existing,_,_ in labels:
                    if label.intersects(existing):
                        label.moveTop(min(plot.bottom()-24,existing.bottom()+3))
                        if label.intersects(existing):
                            label.moveBottom(max(plot.top()+24,existing.top()-3))
                labels.append((label,text,self.COLORS[i%len(self.COLORS)]))
            painter.setPen(QColor('#687568'))
            for j in range(5):
                text = self._time_tick_text(-self.WINDOW_SECONDS+self.WINDOW_SECONDS*j/4, j in (0,4))
                label_width = max(44,painter.fontMetrics().horizontalAdvance(text)+8)
                label_rect = QRectF(plot.left()+plot.width()*j/4-label_width/2,plot.bottom()+4,label_width,18)
                if j == 0:
                    label_rect.moveLeft(plot.left())
                elif j == 4:
                    label_rect.moveRight(plot.right())
                if j == 0 and not self._time_editor.isHidden() and label_rect.intersects(QRectF(self._time_editor.geometry())):
                    continue
                painter.drawText(label_rect,Qt.AlignCenter,text)
            if hover_x is not None and plot.left()<=hover_x<=plot.right():
                painter.setPen(QPen(QColor('#418AC7'),1))
                painter.drawLine(QPointF(hover_x,plot.top()),QPointF(hover_x,plot.bottom()))
            if not dual_axis:
                all_labels.extend(labels)
        for label,text,color in all_labels:
                painter.setPen(QPen(QColor(color),1))
                painter.setBrush(QColor('white'))
                painter.drawRoundedRect(label,3,3)
                painter.drawText(label,Qt.AlignCenter,text)
