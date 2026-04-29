from __future__ import annotations

from collections.abc import Callable
import webbrowser

from PySide6.QtCore import QEvent, QPointF, QRectF, QSize, Qt, QThreadPool, QTimer, Signal
from PySide6.QtGui import QAction, QBrush, QCloseEvent, QColor, QIcon, QPainter, QPen, QPixmap, QPolygonF
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QMenu,
    QPushButton,
    QProgressDialog,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QSystemTrayIcon,
    QTableWidget,
    QTableWidgetItem,
    QStyle,
    QVBoxLayout,
    QWidget,
)

from vynex_vpn_client.app_service import ConnectionResult, VynexAppService, WinwsConflictError
from vynex_vpn_client.constants import APP_DIR, APP_NAME, APP_VERSION
from vynex_vpn_client.models import AppSettings, RuntimeState, ServerEntry, SubscriptionEntry
from vynex_vpn_client.routing_profiles import RoutingProfile
from vynex_vpn_client.tcp_ping import TCP_PING_UNSUPPORTED_ERROR

from . import design_tokens as tokens
from .dialogs import (
    ask_confirmation,
    ask_multiline_text,
    ask_question,
    ask_text,
    show_error_dialog,
    show_info_dialog,
    show_warning_dialog,
)
from .models import DEFAULT_NAVIGATION_ITEMS, NavigationItem
from .workers import FunctionWorker


SERVER_TABLE_ACTIONS_COLUMN = 9
SERVER_TABLE_ID_COLUMN = 10


class ConnectionStatusOrb(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._state = "disconnected"
        self.setObjectName("ConnectionStatusOrb")
        self.setFixedSize(tokens.SPACE_6 * 4, tokens.SPACE_6 * 4)

    def sizeHint(self) -> QSize:
        return QSize(tokens.SPACE_6 * 4, tokens.SPACE_6 * 4)

    def set_state(self, state: str) -> None:
        self._state = state
        self.setProperty("state", state)
        self.update()

    def paintEvent(self, event: QEvent) -> None:  # noqa: ARG002
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        rect = QRectF(tokens.SPACE_2, tokens.SPACE_2, self.width() - tokens.SPACE_4, self.height() - tokens.SPACE_4)
        base_color = QColor(tokens.COLOR_BORDER)
        accent = {
            "connected": QColor(tokens.COLOR_SUCCESS),
            "busy": QColor(tokens.COLOR_INFO),
            "error": QColor(tokens.COLOR_DANGER),
            "disconnected": QColor(tokens.COLOR_DANGER),
        }.get(self._state, QColor(tokens.COLOR_DANGER))

        painter.setPen(QPen(base_color, 5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.setBrush(QColor(tokens.COLOR_SURFACE_ALT))
        painter.drawEllipse(rect)

        span = 330 if self._state == "connected" else 88
        painter.setPen(QPen(accent, 5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawArc(rect, 38 * 16, span * 16)

        center = QPointF(self.width() / 2, self.height() / 2)
        shield = QPolygonF(
            [
                QPointF(center.x(), center.y() - 18),
                QPointF(center.x() + 15, center.y() - 11),
                QPointF(center.x() + 13, center.y() + 8),
                QPointF(center.x(), center.y() + 20),
                QPointF(center.x() - 13, center.y() + 8),
                QPointF(center.x() - 15, center.y() - 11),
            ]
        )
        painter.setBrush(QColor(0, 0, 0, 0))
        painter.setPen(QPen(accent, 3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawPolygon(shield)

        mark_pen = QPen(accent, 3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
        painter.setPen(mark_pen)
        if self._state == "connected":
            painter.drawLine(QPointF(center.x() - 8, center.y() + 2), QPointF(center.x() - 2, center.y() + 9))
            painter.drawLine(QPointF(center.x() - 2, center.y() + 9), QPointF(center.x() + 10, center.y() - 6))
        else:
            painter.drawLine(QPointF(center.x() - 7, center.y() - 6), QPointF(center.x() + 7, center.y() + 8))
            painter.drawLine(QPointF(center.x() + 7, center.y() - 6), QPointF(center.x() - 7, center.y() + 8))


class ServersPageIcon(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(tokens.ICON_SIZE_LG, tokens.ICON_SIZE_LG)

    def paintEvent(self, event: QEvent) -> None:  # noqa: ARG002
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(QColor(tokens.COLOR_PRIMARY_SOFT), 1.7, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        for top in (4, 14):
            painter.drawRoundedRect(QRectF(2.5, top, 17, 6), 1.5, 1.5)
            painter.drawEllipse(QRectF(5, top + 2.1, 1.8, 1.8))
            painter.drawLine(QPointF(10, top + 3), QPointF(16, top + 3))


class ServerSelectionCard(QFrame):
    clicked = Signal(str)

    def __init__(self, server_id: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.server_id = server_id
        self.setObjectName("ServerSelectionCard")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(tokens.SERVER_SELECTION_CARD_HEIGHT)

    def mousePressEvent(self, event: QEvent) -> None:
        self.clicked.emit(self.server_id)
        super().mousePressEvent(event)


class MainWindow(QMainWindow):
    def __init__(self, service: VynexAppService | None = None) -> None:
        super().__init__()
        self.service = service or VynexAppService()
        self.thread_pool = QThreadPool.globalInstance()
        self._active_worker: FunctionWorker | None = None
        self._progress_dialog: QProgressDialog | None = None
        self._force_quit = False
        self._startup_maintenance_started = False
        self._pending_connect_server_id: str | None = None
        self._active_operation_title = ""
        self._active_operation_message = ""
        self._last_operation_error: str | None = None
        self._tray_icon: QSystemTrayIcon | None = None
        self._tray_toggle_action: QAction | None = None
        self._servers: list[ServerEntry] = []
        self._visible_servers: list[ServerEntry] = []
        self._subscriptions: list[SubscriptionEntry] = []
        self._routing_profiles: list[RoutingProfile] = []
        self._settings = AppSettings()
        self._state = RuntimeState()
        self._best_server_id: str | None = None

        self.setWindowTitle(f"{APP_NAME} v{APP_VERSION}")
        self.setMinimumSize(860, 560)
        self.resize(1180, 760)

        icon_path = APP_DIR / "icon.ico"
        if icon_path.exists():
            self.setWindowIcon(QIcon(str(icon_path)))

        self.navigation = QListWidget()
        self.stack = QStackedWidget()
        self.sidebar: QWidget | None = None
        self.content_layout: QVBoxLayout | None = None
        self.content_header: QWidget | None = None

        self.status_value = QLabel("-")
        self.active_server_value = QLabel("-")
        self.mode_value = QLabel("-")
        self.backend_value = QLabel("-")
        self.routing_value = QLabel("-")
        self.pid_value = QLabel("-")
        self.connection_status_orb = ConnectionStatusOrb(self)
        self.connection_status_badge = QLabel("-")
        self.connection_status_detail = QLabel("-")
        self.connection_selected_server_value = QLabel("-")
        self.connection_selected_meta_value = QLabel("-")
        self.connection_selected_ping_value = QLabel("-")
        self.connection_selected_ping_preview_value = QLabel("-")
        self.connection_selected_backend_value = QLabel("-")
        self.connection_selected_mode_value = QLabel("-")
        self.connection_selected_routing_value = QLabel("-")
        self.connection_server_filter = QLineEdit()
        self.connection_subscription_filter = QComboBox()
        self.connection_best_server_button = QPushButton("Лучший ping")
        self.connection_ping_button = QPushButton("Проверить ping")
        self.connection_server_list = QWidget()
        self.connection_server_scroll: QScrollArea | None = None
        self.connection_server_list_layout: QVBoxLayout | None = None
        self.connection_server_count_label = QLabel("-")
        self._connection_selected_server_id: str | None = None
        self._connection_server_cards: dict[str, ServerSelectionCard] = {}
        self.connection_server_preview: QWidget | None = None
        self.server_selector = QComboBox()
        self.connect_button = QPushButton("Подключиться")
        self.refresh_button = QPushButton("Обновить")

        self.servers_filter = QLineEdit()
        self.servers_source_filter = QComboBox()
        self.servers_table = QTableWidget(0, 11)
        self.server_add_button = QPushButton("Добавить")
        self.server_import_button = QPushButton("Быстрый импорт")
        self.server_connect_button = QPushButton("Подключиться")
        self.server_ping_selected_button = QPushButton("Ping выбранный")
        self.server_ping_all_button = QPushButton("Ping все")
        self.server_update_ping_button = QPushButton("Обновить ping")
        self.server_favorite_button = QPushButton("☆ В избранное")
        self.best_server_caption = QLabel("Лучший TCP ping")
        self.best_server_value = QLabel("-")
        self.best_server_ping_badge = QLabel("-")
        self.servers_count_label = QLabel("-")
        self.servers_page_label = QLabel("1")
        self.servers_prev_page_button = QPushButton("‹")
        self.servers_next_page_button = QPushButton("›")
        self.server_details_button = QPushButton("Детали")
        self.server_rename_button = QPushButton("Переименовать")
        self.server_edit_link_button = QPushButton("Изменить ссылку")
        self.server_detach_button = QPushButton("Отвязать")
        self.server_delete_button = QPushButton("Удалить")
        self.subscriptions_table = QTableWidget(0, 7)
        self.subscription_add_button = QPushButton("Добавить")
        self.subscription_refresh_button = QPushButton("Обновить")
        self.subscription_refresh_all_button = QPushButton("Обновить все")
        self.subscription_servers_button = QPushButton("Серверы")
        self.subscription_rename_button = QPushButton("Переименовать")
        self.subscription_edit_url_button = QPushButton("Изменить URL")
        self.subscription_delete_button = QPushButton("Удалить")
        self.components_table = QTableWidget(0, 4)
        self.component_update_button = QPushButton("Обновить выбранный")
        self.component_update_all_button = QPushButton("Обновить все")
        self.app_update_check_button = QPushButton("Проверить обновление приложения")
        self.app_update_open_button = QPushButton("Открыть релиз")
        self.app_self_update_button = QPushButton("Установить обновление")
        self.app_update_status_value = QLabel("-")
        self.settings_mode_combo = QComboBox()
        self.settings_system_proxy_checkbox = QCheckBox("Включать системный proxy Windows в режиме PROXY")
        self.settings_auto_update_checkbox = QCheckBox("Обновлять подписки при запуске")
        self.settings_routing_combo = QComboBox()
        self.settings_routing_description = QLabel("-")
        self.settings_proxy_ports_value = QLabel("HTTP/SOCKS порты выдаются случайно на время подключения")
        self.settings_save_button = QPushButton("Сохранить настройки")
        self.settings_profile_details_button = QPushButton("Описание профиля")
        self.status_details_table = QTableWidget(0, 2)
        self.status_healthcheck_button = QPushButton("Health-check")
        self.servers_empty_state: QFrame | None = None
        self.servers_empty_title: QLabel | None = None
        self.servers_empty_text: QLabel | None = None
        self.subscriptions_empty_state: QFrame | None = None
        self.subscriptions_empty_title: QLabel | None = None
        self.subscriptions_empty_text: QLabel | None = None
        self.components_empty_state: QFrame | None = None
        self.components_empty_title: QLabel | None = None
        self.components_empty_text: QLabel | None = None

        self._setup_widget_defaults()
        self._setup_ui()
        self._setup_tray()
        self.refresh_data()
        QTimer.singleShot(0, self._run_startup_maintenance)

    @staticmethod
    def _line_icon(
        name: str,
        color: str = tokens.COLOR_TEXT_SECONDARY,
        size: int = tokens.ICON_SIZE_MD,
    ) -> QIcon:
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(QColor(color), 1.7, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)

        if name == "search":
            painter.drawEllipse(QRectF(3.2, 3.2, 8.3, 8.3))
            painter.drawLine(QPointF(10.2, 10.2), QPointF(14.6, 14.6))
        elif name == "plus":
            painter.drawLine(QPointF(9, 3.5), QPointF(9, 14.5))
            painter.drawLine(QPointF(3.5, 9), QPointF(14.5, 9))
        elif name == "import":
            painter.drawLine(QPointF(9, 3), QPointF(9, 11))
            painter.drawLine(QPointF(5.5, 8), QPointF(9, 11.5))
            painter.drawLine(QPointF(12.5, 8), QPointF(9, 11.5))
            painter.drawLine(QPointF(4, 14.5), QPointF(14, 14.5))
        elif name == "play":
            painter.drawPolygon(QPolygonF([QPointF(6, 4.5), QPointF(13, 9), QPointF(6, 13.5)]))
        elif name == "pulse":
            painter.drawLine(QPointF(2.8, 10), QPointF(6, 10))
            painter.drawLine(QPointF(6, 10), QPointF(7.6, 5.2))
            painter.drawLine(QPointF(7.6, 5.2), QPointF(10.2, 13))
            painter.drawLine(QPointF(10.2, 13), QPointF(11.7, 8.2))
            painter.drawLine(QPointF(11.7, 8.2), QPointF(15.2, 8.2))
        elif name == "target":
            painter.drawEllipse(QRectF(3.5, 3.5, 11, 11))
            painter.drawEllipse(QRectF(6.8, 6.8, 4.4, 4.4))
            painter.drawPoint(QPointF(9, 9))
        elif name == "refresh":
            painter.drawArc(QRectF(4, 4, 10, 10), 35 * 16, 260 * 16)
            painter.drawLine(QPointF(12.5, 4), QPointF(15, 4.2))
            painter.drawLine(QPointF(14.2, 4.2), QPointF(14.4, 6.8))
        elif name == "info":
            painter.drawEllipse(QRectF(3.5, 3.5, 11, 11))
            painter.drawLine(QPointF(9, 8), QPointF(9, 12))
            painter.drawPoint(QPointF(9, 6))
        elif name == "edit":
            painter.drawLine(QPointF(5, 13), QPointF(13.2, 4.8))
            painter.drawLine(QPointF(11.2, 4.5), QPointF(13.5, 6.8))
            painter.drawLine(QPointF(4.4, 13.8), QPointF(7.4, 13))
        elif name == "link":
            painter.drawRoundedRect(QRectF(3.3, 7.2, 6.7, 4.6), 2.2, 2.2)
            painter.drawRoundedRect(QRectF(8.1, 6.2, 6.7, 4.6), 2.2, 2.2)
        elif name == "unlink":
            painter.drawRoundedRect(QRectF(3.3, 7.2, 6.7, 4.6), 2.2, 2.2)
            painter.drawRoundedRect(QRectF(8.1, 6.2, 6.7, 4.6), 2.2, 2.2)
            painter.drawLine(QPointF(13.8, 3.8), QPointF(4.2, 14.2))
        elif name == "trash":
            painter.drawLine(QPointF(5, 6), QPointF(13, 6))
            painter.drawLine(QPointF(7, 4), QPointF(11, 4))
            painter.drawRect(QRectF(6, 7, 6, 7.5))
        elif name == "filter":
            painter.drawPolygon(QPolygonF([QPointF(3.5, 4.5), QPointF(14.5, 4.5), QPointF(10.2, 9.5), QPointF(10.2, 13.5), QPointF(7.8, 14.8), QPointF(7.8, 9.5)]))
        elif name == "server":
            for top in (4.2, 10.2):
                painter.drawRoundedRect(QRectF(3, top, 12, 4.2), 1.2, 1.2)
                painter.drawEllipse(QRectF(5, top + 1.35, 1.4, 1.4))
                painter.drawLine(QPointF(8.2, top + 2.1), QPointF(13, top + 2.1))
        elif name == "latency":
            painter.drawLine(QPointF(2.8, 10), QPointF(5.6, 10))
            painter.drawLine(QPointF(5.6, 10), QPointF(7.2, 5.5))
            painter.drawLine(QPointF(7.2, 5.5), QPointF(10, 13.2))
            painter.drawLine(QPointF(10, 13.2), QPointF(11.4, 8.4))
            painter.drawLine(QPointF(11.4, 8.4), QPointF(15.2, 8.4))
        elif name == "engine":
            painter.drawEllipse(QRectF(5.2, 5.2, 7.6, 7.6))
            painter.drawEllipse(QRectF(7.6, 7.6, 2.8, 2.8))
            for start, end in (
                (QPointF(9, 2.8), QPointF(9, 4.7)),
                (QPointF(9, 13.3), QPointF(9, 15.2)),
                (QPointF(2.8, 9), QPointF(4.7, 9)),
                (QPointF(13.3, 9), QPointF(15.2, 9)),
                (QPointF(4.6, 4.6), QPointF(5.9, 5.9)),
                (QPointF(12.1, 12.1), QPointF(13.4, 13.4)),
                (QPointF(13.4, 4.6), QPointF(12.1, 5.9)),
                (QPointF(5.9, 12.1), QPointF(4.6, 13.4)),
            ):
                painter.drawLine(start, end)
        elif name == "mode":
            painter.drawEllipse(QRectF(3.2, 11.2, 2.8, 2.8))
            painter.drawEllipse(QRectF(12, 4, 2.8, 2.8))
            painter.drawLine(QPointF(6, 12.6), QPointF(8.2, 12.6))
            painter.drawLine(QPointF(8.2, 12.6), QPointF(8.2, 6))
            painter.drawLine(QPointF(8.2, 6), QPointF(12, 5.4))
        elif name == "profile":
            painter.drawPolygon(
                QPolygonF(
                    [
                        QPointF(9, 3.2),
                        QPointF(10.8, 7),
                        QPointF(15, 7.5),
                        QPointF(12, 10.4),
                        QPointF(12.7, 14.6),
                        QPointF(9, 12.6),
                        QPointF(5.3, 14.6),
                        QPointF(6, 10.4),
                        QPointF(3, 7.5),
                        QPointF(7.2, 7),
                    ]
                )
            )
        elif name == "pid":
            painter.drawLine(QPointF(6.5, 4), QPointF(5.5, 14))
            painter.drawLine(QPointF(11.5, 4), QPointF(10.5, 14))
            painter.drawLine(QPointF(4, 7), QPointF(14, 7))
            painter.drawLine(QPointF(3.5, 11), QPointF(13.5, 11))

        painter.end()
        return QIcon(pixmap)

    def _setup_widget_defaults(self) -> None:
        self.connect_button.setObjectName("PrimaryButton")
        self.connection_best_server_button.setObjectName("PrimaryButton")
        self.connection_ping_button.setObjectName("OutlinedButton")
        self.refresh_button.setObjectName("SubtleButton")
        self.server_add_button.setObjectName("SubtleButton")
        self.server_import_button.setObjectName("SubtleButton")
        self.server_connect_button.setObjectName("PrimaryButton")
        self.server_ping_all_button.setObjectName("PrimaryButton")
        self.server_update_ping_button.setObjectName("SubtleButton")
        self.server_favorite_button.setObjectName("FavoriteButton")
        self.servers_page_label.setObjectName("PagerCurrent")
        self.servers_prev_page_button.setObjectName("PagerButton")
        self.servers_next_page_button.setObjectName("PagerButton")
        self.server_delete_button.setObjectName("DangerButton")
        self.subscription_delete_button.setObjectName("DangerButton")
        self.app_self_update_button.setObjectName("PrimaryButton")

        for button in (
            self.connect_button,
            self.connection_best_server_button,
            self.connection_ping_button,
            self.refresh_button,
            self.server_add_button,
            self.server_import_button,
            self.server_connect_button,
            self.server_ping_selected_button,
            self.server_ping_all_button,
            self.server_update_ping_button,
            self.server_favorite_button,
            self.servers_prev_page_button,
            self.servers_next_page_button,
            self.server_details_button,
            self.server_rename_button,
            self.server_edit_link_button,
            self.server_detach_button,
            self.server_delete_button,
            self.subscription_add_button,
            self.subscription_refresh_button,
            self.subscription_refresh_all_button,
            self.subscription_servers_button,
            self.subscription_rename_button,
            self.subscription_edit_url_button,
            self.subscription_delete_button,
            self.component_update_button,
            self.component_update_all_button,
            self.app_update_check_button,
            self.app_update_open_button,
            self.app_self_update_button,
            self.settings_save_button,
            self.settings_profile_details_button,
            self.status_healthcheck_button,
        ):
            button.setAutoDefault(False)
            button.setMinimumWidth(max(button.minimumWidth(), tokens.BUTTON_MIN_WIDTH))

        self.app_self_update_button.setMinimumWidth(tokens.SPACE_12 * 4)
        self.app_update_check_button.setMinimumWidth(tokens.SPACE_6 * 10)
        self.connect_button.setMinimumWidth(tokens.SPACE_3 * 13)
        self.connect_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPlay))
        self.connection_best_server_button.setToolTip("Автоматически выбрать сервер с наименьшей задержкой")
        self.connection_best_server_button.setIcon(self._line_icon("target", tokens.COLOR_TEXT_INVERSE))
        self.connection_ping_button.setIcon(self._line_icon("latency", tokens.COLOR_PRIMARY_SOFT))
        self.connection_best_server_button.setIconSize(QSize(tokens.ICON_SIZE_SM, tokens.ICON_SIZE_SM))
        self.connection_ping_button.setIconSize(QSize(tokens.ICON_SIZE_SM, tokens.ICON_SIZE_SM))
        self.refresh_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_BrowserReload))
        self.servers_filter.addAction(
            self._line_icon("search", tokens.COLOR_TEXT_MUTED),
            QLineEdit.ActionPosition.LeadingPosition,
        )
        server_icons = {
            self.server_connect_button: "play",
            self.server_ping_selected_button: "pulse",
            self.server_ping_all_button: "target",
            self.server_update_ping_button: "refresh",
            self.server_add_button: "plus",
            self.server_import_button: "import",
            self.server_details_button: "info",
            self.server_rename_button: "edit",
            self.server_edit_link_button: "link",
            self.server_detach_button: "unlink",
            self.server_delete_button: "trash",
        }
        for button, icon_name in server_icons.items():
            color = tokens.COLOR_DANGER if button is self.server_delete_button else tokens.COLOR_TEXT_SECONDARY
            button.setIcon(self._line_icon(icon_name, color))
            button.setIconSize(QSize(tokens.ICON_SIZE_SM, tokens.ICON_SIZE_SM))
        self.server_import_button.setText("Быстрый импорт")
        server_action_buttons = (
            self.server_add_button,
            self.server_import_button,
            self.server_ping_selected_button,
            self.server_ping_all_button,
            self.server_update_ping_button,
            self.server_details_button,
            self.server_favorite_button,
            self.server_rename_button,
            self.server_edit_link_button,
            self.server_detach_button,
            self.server_delete_button,
        )
        for button in server_action_buttons:
            button.setMinimumWidth(max(tokens.BUTTON_MIN_WIDTH, button.sizeHint().width() + tokens.SPACE_2))
            button.setFixedHeight(tokens.CONTROL_HEIGHT)
        self.server_connect_button.setMinimumWidth(tokens.SPACE_8 * 4)
        self.server_connect_button.setFixedHeight(tokens.CONTROL_HEIGHT)
        self.servers_prev_page_button.setFixedSize(tokens.PAGER_SIZE, tokens.PAGER_SIZE)
        self.servers_next_page_button.setFixedSize(tokens.PAGER_SIZE, tokens.PAGER_SIZE)
        self.servers_page_label.setFixedSize(tokens.PAGER_SIZE, tokens.PAGER_SIZE)
        self.servers_page_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.servers_prev_page_button.setEnabled(False)
        self.servers_next_page_button.setEnabled(False)

        self.connection_status_badge.setObjectName("ConnectionStatusBadge")
        self.connection_status_badge.setMinimumWidth(tokens.SPACE_5 * 11)
        self.connection_status_badge.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.connection_status_detail.setObjectName("ConnectionStatusDetail")
        self.connection_selected_server_value.setObjectName("ConnectionServerTitle")
        self.connection_selected_meta_value.setObjectName("ConnectionServerMeta")
        self.connection_selected_ping_preview_value.setObjectName("FieldValue")
        self.connection_server_count_label.setObjectName("ConnectionServerCount")
        self.connection_server_list.setObjectName("ConnectionServerList")
        self.server_selector.setMinimumContentsLength(34)
        self.server_selector.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.connection_server_filter.setClearButtonEnabled(True)
        self.connection_server_filter.setMinimumWidth(tokens.SPACE_5 * 13)
        self.connection_subscription_filter.setMinimumWidth(tokens.SPACE_4 * 12)
        self.servers_filter.setClearButtonEnabled(True)
        self.servers_filter.setMinimumWidth(tokens.SPACE_6 * 10)
        self.servers_filter.setFixedHeight(tokens.CONTROL_HEIGHT)
        self.servers_source_filter.setMinimumWidth(tokens.SPACE_4 * 12)
        self.servers_source_filter.setFixedWidth(tokens.SPACE_4 * 12)
        self.servers_source_filter.setFixedHeight(tokens.CONTROL_HEIGHT)
        self.servers_source_filter.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        filter_icon = self._line_icon("filter", tokens.COLOR_TEXT_SECONDARY)
        self.servers_source_filter.addItem(filter_icon, "Фильтр: Все", "all")
        self.servers_source_filter.addItem(filter_icon, "Фильтр: Избранное", "favorite")
        self.servers_source_filter.addItem(filter_icon, "Фильтр: Ручной", "manual")
        self.servers_source_filter.addItem(filter_icon, "Фильтр: Подписка", "subscription")
        self.servers_count_label.setObjectName("ConnectionServerCount")
        self.best_server_caption.setObjectName("BestServerCaption")
        self.best_server_value.setObjectName("BestServerName")
        self.best_server_value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.best_server_ping_badge.setObjectName("BestPingBadge")
        self.best_server_ping_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.best_server_ping_badge.setMinimumWidth(tokens.SPACE_4 * 4)
        self.best_server_ping_badge.setFixedHeight(tokens.CONTROL_HEIGHT_COMPACT)
        self.settings_proxy_ports_value.setWordWrap(True)
        self.settings_routing_description.setWordWrap(True)
        self.app_update_status_value.setWordWrap(True)
        for label in (
            self.status_value,
            self.active_server_value,
            self.mode_value,
            self.backend_value,
            self.routing_value,
            self.pid_value,
            self.connection_status_detail,
            self.connection_server_count_label,
            self.connection_selected_ping_value,
            self.connection_selected_ping_preview_value,
            self.connection_selected_backend_value,
            self.connection_selected_mode_value,
            self.connection_selected_routing_value,
        ):
            label.setWordWrap(True)
        self.best_server_value.setWordWrap(False)
        for label in (self.connection_selected_server_value, self.connection_selected_meta_value):
            label.setWordWrap(False)
            label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def _setup_ui(self) -> None:
        central = QWidget(self)
        root_layout = QHBoxLayout(central)
        root_layout.setContentsMargins(*tokens.spacing(tokens.SPACE_0, tokens.SPACE_0, tokens.SPACE_0, tokens.SPACE_0))
        root_layout.setSpacing(tokens.SPACE_0)

        root_layout.addWidget(self._build_sidebar())
        root_layout.addWidget(self._build_content(), 1)
        self.setCentralWidget(central)

        self.connect_button.clicked.connect(self._toggle_connection)
        self.refresh_button.clicked.connect(self.refresh_data)
        self.connection_server_filter.textChanged.connect(self._update_server_selector)
        self.connection_subscription_filter.currentIndexChanged.connect(self._update_server_selector)
        self.connection_best_server_button.clicked.connect(self._select_best_connection_server)
        self.connection_ping_button.clicked.connect(self._ping_connection_servers)
        self.servers_filter.textChanged.connect(self._update_servers_table)
        self.servers_source_filter.currentIndexChanged.connect(self._update_servers_table)
        self.servers_table.itemSelectionChanged.connect(self._update_server_action_state)
        self.servers_table.itemClicked.connect(self._on_servers_table_item_clicked)
        self.servers_table.itemDoubleClicked.connect(self._on_servers_table_item_double_clicked)
        self.servers_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.servers_table.customContextMenuRequested.connect(self._show_server_table_context_menu)
        self.server_add_button.clicked.connect(self._add_server_from_share_link)
        self.server_import_button.clicked.connect(self._quick_import_servers)
        self.server_connect_button.clicked.connect(self._connect_best_server)
        self.server_ping_selected_button.clicked.connect(self._ping_selected_server)
        self.server_ping_all_button.clicked.connect(self._ping_all_servers)
        self.server_update_ping_button.clicked.connect(self._ping_all_servers)
        self.server_favorite_button.clicked.connect(self._toggle_selected_server_favorite)
        self.server_details_button.clicked.connect(self._show_selected_server_details)
        self.server_rename_button.clicked.connect(self._rename_selected_server)
        self.server_edit_link_button.clicked.connect(self._edit_selected_server_link)
        self.server_detach_button.clicked.connect(self._detach_selected_server)
        self.server_delete_button.clicked.connect(self._delete_selected_server)
        self.subscriptions_table.itemSelectionChanged.connect(self._update_subscription_action_state)
        self.subscriptions_table.itemDoubleClicked.connect(lambda _item: self._show_selected_subscription_servers())
        self.subscription_add_button.clicked.connect(self._add_subscription)
        self.subscription_refresh_button.clicked.connect(self._refresh_selected_subscription)
        self.subscription_refresh_all_button.clicked.connect(self._refresh_all_subscriptions)
        self.subscription_servers_button.clicked.connect(self._show_selected_subscription_servers)
        self.subscription_rename_button.clicked.connect(self._rename_selected_subscription)
        self.subscription_edit_url_button.clicked.connect(self._edit_selected_subscription_url)
        self.subscription_delete_button.clicked.connect(self._delete_selected_subscription)
        self.settings_save_button.clicked.connect(self._save_settings_from_form)
        self.settings_profile_details_button.clicked.connect(self._show_selected_routing_profile_details)
        self.settings_routing_combo.currentIndexChanged.connect(self._update_selected_routing_profile_description)
        self.components_table.itemSelectionChanged.connect(self._update_component_action_state)
        self.component_update_button.clicked.connect(self._update_selected_component)
        self.component_update_all_button.clicked.connect(self._update_all_components)
        self.app_update_check_button.clicked.connect(self._check_app_update)
        self.app_update_open_button.clicked.connect(self._open_app_release_page)
        self.app_self_update_button.clicked.connect(self._prepare_self_update)
        self.status_healthcheck_button.clicked.connect(self._run_status_healthcheck)
        if self.navigation.count():
            self.navigation.setCurrentRow(0)

    def _setup_tray(self) -> None:
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        icon = self.windowIcon()
        if icon.isNull():
            icon = QIcon(str(APP_DIR / "icon.ico"))
        tray = QSystemTrayIcon(icon, self)
        tray.setToolTip(f"{APP_NAME} v{APP_VERSION}")

        from PySide6.QtWidgets import QMenu

        menu = QMenu(self)
        tray.setContextMenu(menu)
        open_action = QAction("Открыть", self)
        self._tray_toggle_action = QAction("Подключиться", self)
        status_action = QAction("Статус", self)
        quit_action = QAction("Выход", self)

        open_action.triggered.connect(self.show_normal)
        self._tray_toggle_action.triggered.connect(self._toggle_connection)
        status_action.triggered.connect(self.show_status_page)
        quit_action.triggered.connect(self.request_exit)
        tray.activated.connect(self._on_tray_activated)

        menu.addAction(open_action)
        menu.addAction(self._tray_toggle_action)
        menu.addAction(status_action)
        menu.addSeparator()
        menu.addAction(quit_action)
        tray.show()
        self._tray_icon = tray
        self._update_tray_actions()

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._force_quit:
            event.accept()
            return
        if self._tray_icon is not None and self._tray_icon.isVisible():
            result = ask_question(
                self,
                "Закрытие Vynex",
                "Свернуть приложение в tray?\n\nПриложение продолжит работать в фоне.",
                buttons=QMessageBox.StandardButton.Yes
                | QMessageBox.StandardButton.No
                | QMessageBox.StandardButton.Cancel,
                default_button=QMessageBox.StandardButton.Yes,
                escape_button=QMessageBox.StandardButton.Cancel,
                button_texts={
                    QMessageBox.StandardButton.Yes: "Свернуть",
                    QMessageBox.StandardButton.No: "Выйти",
                    QMessageBox.StandardButton.Cancel: "Отмена",
                },
            )
            if result == QMessageBox.StandardButton.Yes:
                event.ignore()
                self.hide()
                self._tray_icon.showMessage(
                    APP_NAME,
                    "Vynex продолжает работать в tray.",
                    QSystemTrayIcon.MessageIcon.Information,
                    2500,
                )
                return
            if result == QMessageBox.StandardButton.Cancel:
                event.ignore()
                return
        event.ignore()
        self.request_exit()

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.WindowStateChange:
            self._update_tray_actions()

    def show_normal(self) -> None:
        self.show()
        self.setWindowState(self.windowState() & ~Qt.WindowState.WindowMinimized)
        self.raise_()
        self.activateWindow()

    def show_status_page(self) -> None:
        self.show_normal()
        for index in range(self.navigation.count()):
            item = self.navigation.item(index)
            if item.data(Qt.ItemDataRole.UserRole) == "status":
                self.navigation.setCurrentRow(index)
                break

    def request_exit(self) -> None:
        if self._active_worker is not None:
            show_warning_dialog(
                self,
                "Операция выполняется",
                "Дождитесь завершения текущей операции перед выходом.",
            )
            return
        try:
            self._state = self.service.get_current_state()
        except Exception as exc:  # noqa: BLE001
            if not ask_confirmation(
                self,
                "Ошибка проверки состояния",
                f"Не удалось проверить runtime state: {exc}\n\nВыйти из приложения?",
                default_yes=False,
            ):
                return
        if self._state.is_running:
            result = ask_question(
                self,
                "Активное подключение",
                "VPN-подключение активно. Отключить VPN перед выходом?\n\n"
                "Можно корректно остановить VPN или выйти, оставив runtime активным.",
                buttons=QMessageBox.StandardButton.Yes
                | QMessageBox.StandardButton.No
                | QMessageBox.StandardButton.Cancel,
                default_button=QMessageBox.StandardButton.Yes,
                escape_button=QMessageBox.StandardButton.Cancel,
                button_texts={
                    QMessageBox.StandardButton.Yes: "Отключить и выйти",
                    QMessageBox.StandardButton.No: "Выйти без отключения",
                    QMessageBox.StandardButton.Cancel: "Отмена",
                },
            )
            if result == QMessageBox.StandardButton.Cancel:
                return
            if result == QMessageBox.StandardButton.Yes:
                self._run_background_operation(
                    "Выход",
                    "Отключаем VPN перед выходом...",
                    lambda: self.service.disconnect(),
                    lambda _result: self._finish_exit(),
                )
                return
        self._finish_exit()

    def _finish_exit(self) -> None:
        self._force_quit = True
        if self._tray_icon is not None:
            self._tray_icon.hide()
        self.thread_pool.waitForDone(1000)
        QApplication.quit()

    def _on_tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason in {
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        }:
            self.show_normal()

    def _update_tray_actions(self) -> None:
        if self._tray_toggle_action is not None:
            self._tray_toggle_action.setText("Отключиться" if self._state.is_running else "Подключиться")

    def _build_sidebar(self) -> QWidget:
        sidebar = QFrame(self)
        sidebar.setObjectName("Sidebar")
        self.sidebar = sidebar
        sidebar.setFixedWidth(tokens.SPACE_7 * 8)
        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(*tokens.spacing(tokens.SPACE_5, tokens.SPACE_6, tokens.SPACE_4, tokens.SPACE_5))
        layout.setSpacing(tokens.SPACE_3)

        title = QLabel(APP_NAME)
        title.setObjectName("AppTitle")
        title.setWordWrap(True)

        self.navigation.setObjectName("Navigation")
        self.navigation.setFrameShape(QFrame.Shape.NoFrame)
        self.navigation.setSpacing(tokens.SPACE_1)
        self.navigation.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        layout.addWidget(title)
        layout.addWidget(self.navigation, 1)
        return sidebar

    def _build_content(self) -> QWidget:
        content = QWidget(self)
        content.setObjectName("Content")
        layout = QVBoxLayout(content)
        self.content_layout = layout
        layout.setContentsMargins(*tokens.spacing(tokens.SPACE_6, tokens.SPACE_6, tokens.SPACE_6, tokens.SPACE_6))
        layout.setSpacing(tokens.SPACE_5)

        header_container = QWidget(self)
        header_container.setObjectName("ContentHeader")
        header_layout = QHBoxLayout()
        header_layout.setContentsMargins(*tokens.spacing(tokens.SPACE_0, tokens.SPACE_0, tokens.SPACE_0, tokens.SPACE_0))
        header_layout.setSpacing(tokens.SPACE_3)
        header_container.setLayout(header_layout)
        self.content_header = header_container
        header = QLabel(f"{APP_NAME} v{APP_VERSION}")
        header.setObjectName("HeaderTitle")
        header.setWordWrap(False)
        header_layout.addWidget(header)
        header_layout.addStretch(1)
        header_layout.addWidget(self.refresh_button)

        layout.addWidget(header_container)
        layout.addWidget(self.stack, 1)

        pages = {
            "connect": self._build_connection_page,
            "servers": self._build_servers_page,
            "subscriptions": self._build_subscriptions_page,
            "settings": self._build_settings_page,
            "components": self._build_components_page,
            "status": self._build_status_page,
        }
        for item in DEFAULT_NAVIGATION_ITEMS:
            list_item = QListWidgetItem(item.title)
            list_item.setData(Qt.ItemDataRole.UserRole, item.key)
            list_item.setToolTip(item.subtitle)
            list_item.setIcon(self._navigation_icon(item.key))
            self.navigation.addItem(list_item)
            self.stack.addWidget(pages[item.key](item))
        self.navigation.currentRowChanged.connect(self._on_navigation_changed)
        return content

    def _on_navigation_changed(self, row: int) -> None:
        self.stack.setCurrentIndex(row)
        item = self.navigation.item(row)
        is_servers = bool(item and item.data(Qt.ItemDataRole.UserRole) == "servers")
        if self.sidebar is not None:
            self.sidebar.setVisible(True)
        if self.content_header is not None:
            self.content_header.setVisible(not is_servers)
        if self.content_layout is not None:
            if is_servers:
                self.content_layout.setContentsMargins(
                    *tokens.spacing(tokens.SPACE_4, tokens.SPACE_3, tokens.SPACE_4, tokens.SPACE_1)
                )
                self.content_layout.setSpacing(tokens.SPACE_0)
            else:
                self.content_layout.setContentsMargins(
                    *tokens.spacing(tokens.SPACE_6, tokens.SPACE_6, tokens.SPACE_6, tokens.SPACE_6)
                )
                self.content_layout.setSpacing(tokens.SPACE_5)

    def _navigation_icon(self, key: str) -> QIcon:
        icons = {
            "connect": QStyle.StandardPixmap.SP_DriveNetIcon,
            "servers": QStyle.StandardPixmap.SP_ComputerIcon,
            "subscriptions": QStyle.StandardPixmap.SP_FileDialogContentsView,
            "settings": QStyle.StandardPixmap.SP_FileDialogDetailedView,
            "components": QStyle.StandardPixmap.SP_DirIcon,
            "status": QStyle.StandardPixmap.SP_MessageBoxInformation,
        }
        return self.style().standardIcon(icons.get(key, QStyle.StandardPixmap.SP_FileIcon))

    def _build_connection_page(self, item: NavigationItem) -> QWidget:
        page = self._page_shell(item)
        layout = page.layout()
        assert isinstance(layout, QVBoxLayout)

        hero = QFrame(self)
        hero.setObjectName("ConnectionHero")
        hero_layout = QVBoxLayout(hero)
        hero_layout.setContentsMargins(*tokens.spacing(tokens.SPACE_7, tokens.SPACE_5, tokens.SPACE_7, tokens.SPACE_5))
        hero_layout.setSpacing(tokens.SPACE_5)

        hero_top = QHBoxLayout()
        hero_top.setContentsMargins(*tokens.spacing(tokens.SPACE_0, tokens.SPACE_0, tokens.SPACE_0, tokens.SPACE_0))
        hero_top.setSpacing(tokens.SPACE_5)

        status_group = QHBoxLayout()
        status_group.setContentsMargins(*tokens.spacing(tokens.SPACE_0, tokens.SPACE_0, tokens.SPACE_0, tokens.SPACE_0))
        status_group.setSpacing(tokens.SPACE_5)
        status_group.addWidget(self.connection_status_orb, 0, Qt.AlignmentFlag.AlignVCenter)

        status_layout = QVBoxLayout()
        status_layout.setContentsMargins(*tokens.spacing(tokens.SPACE_0, tokens.SPACE_0, tokens.SPACE_0, tokens.SPACE_0))
        status_layout.setSpacing(tokens.SPACE_1)
        status_layout.addWidget(self._panel_title("Состояние VPN"))
        status_layout.addWidget(self.connection_status_badge)
        status_layout.addWidget(self.connection_status_detail)

        status_layout.addStretch(1)
        status_group.addLayout(status_layout, 1)

        stats = QHBoxLayout()
        stats.setContentsMargins(*tokens.spacing(tokens.SPACE_0, tokens.SPACE_0, tokens.SPACE_0, tokens.SPACE_0))
        stats.setSpacing(tokens.SPACE_0)
        self._add_hero_metric(stats, "server", "Активный сервер", self.active_server_value, tokens.COLOR_PRIMARY_SOFT)
        self._add_hero_metric(stats, "latency", "TCP ping", self.connection_selected_ping_value, tokens.COLOR_PRIMARY_SOFT)
        self._add_hero_metric(stats, "engine", "Движок", self.backend_value, tokens.COLOR_PRIMARY_SOFT)
        self._add_hero_metric(stats, "mode", "Режим", self.mode_value, tokens.COLOR_PRIMARY_SOFT)
        self._add_hero_metric(stats, "profile", "Профиль", self.routing_value, tokens.COLOR_PRIMARY_SOFT)
        self._add_hero_metric(stats, "pid", "PID", self.pid_value, tokens.COLOR_PRIMARY_SOFT, add_separator=False)

        action_layout = QVBoxLayout()
        action_layout.setContentsMargins(*tokens.spacing(tokens.SPACE_0, tokens.SPACE_0, tokens.SPACE_0, tokens.SPACE_0))
        action_layout.setSpacing(tokens.SPACE_3)
        action_layout.addWidget(self.connect_button)
        action_layout.addStretch(1)

        hero_top.addLayout(status_group, 1)
        hero_top.addStretch(1)
        hero_top.addLayout(action_layout, 0)
        hero_layout.addLayout(hero_top)
        hero_layout.addWidget(self._hero_separator())
        hero_layout.addLayout(stats)

        selector = QFrame(self)
        selector.setObjectName("Panel")
        selector.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        selector_layout = QVBoxLayout(selector)
        selector_layout.setContentsMargins(*tokens.spacing(tokens.SPACE_4, tokens.SPACE_4, tokens.SPACE_4, tokens.SPACE_4))
        selector_layout.setSpacing(tokens.SPACE_3)
        selector_layout.addWidget(self._panel_title("Выбор сервера"))

        self.connection_server_filter.setPlaceholderText("Поиск по имени, протоколу, host или подписке")
        filter_row = QHBoxLayout()
        filter_row.setContentsMargins(*tokens.spacing(tokens.SPACE_0, tokens.SPACE_0, tokens.SPACE_0, tokens.SPACE_0))
        filter_row.setSpacing(tokens.SPACE_3)
        filter_row.addWidget(self.connection_server_filter, 1)
        filter_row.addWidget(self.connection_subscription_filter, 0)
        selector_layout.addLayout(filter_row)

        self.connection_server_list_layout = QVBoxLayout(self.connection_server_list)
        self.connection_server_list_layout.setContentsMargins(
            *tokens.spacing(tokens.SPACE_0, tokens.SPACE_0, tokens.SPACE_0, tokens.SPACE_0)
        )
        self.connection_server_list_layout.setSpacing(tokens.SPACE_2)

        server_scroll = QScrollArea(self)
        server_scroll.setObjectName("ConnectionServerScroll")
        server_scroll.viewport().setObjectName("ConnectionServerViewport")
        server_scroll.setWidgetResizable(True)
        server_scroll.setFrameShape(QFrame.Shape.NoFrame)
        server_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        server_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        server_scroll.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        server_scroll.setMinimumHeight(tokens.CONNECTION_SERVER_LIST_HEIGHT)
        server_scroll.setWidget(self.connection_server_list)
        self.connection_server_scroll = server_scroll
        selector_layout.addWidget(server_scroll, 1)

        selector_footer = QHBoxLayout()
        selector_footer.setContentsMargins(*tokens.spacing(tokens.SPACE_0, tokens.SPACE_1, tokens.SPACE_0, tokens.SPACE_0))
        selector_footer.setSpacing(tokens.SPACE_3)
        selector_footer.addWidget(self.connection_server_count_label)
        selector_footer.addStretch(1)
        selector_footer.addWidget(self.connection_best_server_button)
        selector_footer.addWidget(self.connection_ping_button)
        selector_layout.addLayout(selector_footer)

        layout.addWidget(hero)
        layout.addWidget(selector, 1)
        return page

    def _build_servers_page(self, item: NavigationItem) -> QWidget:
        page = QWidget(self)
        page.setObjectName("ServersPage")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(*tokens.spacing(tokens.SPACE_0, tokens.SPACE_0, tokens.SPACE_0, tokens.SPACE_0))
        layout.setSpacing(tokens.SPACE_3)

        header_frame = QWidget(self)
        header_frame.setObjectName("ServersPageHeader")
        header_layout = QHBoxLayout(header_frame)
        header_layout.setContentsMargins(*tokens.spacing(tokens.SPACE_0, tokens.SPACE_0, tokens.SPACE_0, tokens.SPACE_0))
        header_layout.setSpacing(tokens.SPACE_3)
        header_layout.addWidget(ServersPageIcon(self), 0, Qt.AlignmentFlag.AlignVCenter)

        title_layout = QVBoxLayout()
        title_layout.setContentsMargins(*tokens.spacing(tokens.SPACE_0, tokens.SPACE_0, tokens.SPACE_0, tokens.SPACE_0))
        title_layout.setSpacing(tokens.SPACE_1)
        title = QLabel(item.title)
        title.setObjectName("PageTitle")
        subtitle = QLabel(item.subtitle)
        subtitle.setObjectName("PageSubtitle")
        title_layout.addWidget(title)
        title_layout.addWidget(subtitle)
        header_layout.addLayout(title_layout, 1)
        layout.addWidget(header_frame)

        self._configure_table(
            self.servers_table,
            (
                "",
                "",
                "Название",
                "Протокол",
                "Host",
                "Port",
                "Источник",
                "TCP ping",
                "Статус",
                "Действия",
                "ID",
            ),
        )
        self.servers_table.setObjectName("ServersTable")
        self.servers_table.setColumnHidden(SERVER_TABLE_ID_COLUMN, True)
        self.servers_table.verticalHeader().setDefaultSectionSize(tokens.SERVERS_TABLE_ROW_HEIGHT)
        self.servers_table.verticalHeader().setMinimumSectionSize(tokens.SERVERS_TABLE_ROW_HEIGHT)
        self.servers_table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        header = self.servers_table.horizontalHeader()
        header.setStretchLastSection(False)
        header.setMinimumSectionSize(tokens.PAGER_SIZE)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(5, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(6, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(7, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(8, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(9, QHeaderView.ResizeMode.Fixed)
        header.resizeSection(0, tokens.SERVER_TABLE_CHECK_COLUMN_WIDTH)
        header.resizeSection(1, tokens.PAGER_SIZE)
        header.resizeSection(3, tokens.SERVER_TABLE_PROTOCOL_COLUMN_WIDTH)
        header.resizeSection(5, tokens.SPACE_7 * 2)
        header.resizeSection(6, tokens.SPACE_6 * 4)
        header.resizeSection(7, tokens.SPACE_5 * 4)
        header.resizeSection(8, tokens.SPACE_4 * 4)
        header.resizeSection(9, tokens.SPACE_4 * 4)

        search_card = QFrame(self)
        search_card.setObjectName("ServersSearchCard")
        search_layout = QHBoxLayout(search_card)
        search_layout.setContentsMargins(*tokens.spacing(tokens.SPACE_4, tokens.SPACE_3, tokens.SPACE_4, tokens.SPACE_3))
        search_layout.setSpacing(tokens.SPACE_3)
        self.servers_filter.setPlaceholderText("Поиск по имени, протоколу, host, source или подписке")
        search_layout.addWidget(self.servers_filter, 1)
        search_layout.addWidget(self.servers_source_filter, 0)

        action_bar = QFrame(self)
        action_bar.setObjectName("ServersActionsBar")
        action_layout = QHBoxLayout(action_bar)
        action_layout.setContentsMargins(*tokens.spacing(tokens.SPACE_0, tokens.SPACE_0, tokens.SPACE_0, tokens.SPACE_0))
        action_layout.setSpacing(tokens.SPACE_3)
        action_layout.addWidget(self.server_add_button)
        action_layout.addWidget(self.server_import_button)
        action_layout.addWidget(self.server_ping_all_button)
        action_layout.addWidget(self.server_update_ping_button)
        action_layout.addStretch(1)

        best_card = QFrame(self)
        best_card.setObjectName("ServersBestCard")
        best_layout = QHBoxLayout(best_card)
        best_layout.setContentsMargins(*tokens.spacing(tokens.SPACE_4, tokens.SPACE_4, tokens.SPACE_4, tokens.SPACE_4))
        best_layout.setSpacing(tokens.SPACE_3)
        best_text_layout = QVBoxLayout()
        best_text_layout.setContentsMargins(*tokens.spacing(tokens.SPACE_0, tokens.SPACE_0, tokens.SPACE_0, tokens.SPACE_0))
        best_text_layout.setSpacing(tokens.SPACE_1)
        best_text_layout.addWidget(self.best_server_caption)
        best_text_layout.addWidget(self.best_server_value)
        best_layout.addLayout(best_text_layout, 1)
        best_layout.addWidget(self.best_server_ping_badge, 0, Qt.AlignmentFlag.AlignVCenter)
        best_layout.addWidget(self.server_connect_button, 0, Qt.AlignmentFlag.AlignVCenter)

        footer = self._toolbar()
        footer.setObjectName("ServersFooter")
        footer_layout = footer.layout()
        assert isinstance(footer_layout, QHBoxLayout)
        footer_layout.setContentsMargins(*tokens.spacing(tokens.SPACE_0, tokens.SPACE_0, tokens.SPACE_0, tokens.SPACE_0))
        footer_layout.addWidget(self.servers_count_label)
        footer_layout.addStretch(1)
        footer_layout.addWidget(self.servers_prev_page_button)
        footer_layout.addWidget(self.servers_page_label)
        footer_layout.addWidget(self.servers_next_page_button)

        (
            self.servers_empty_state,
            self.servers_empty_title,
            self.servers_empty_text,
        ) = self._empty_state(
            "Серверов пока нет",
            "Добавьте сервер вручную или импортируйте ссылку, подписку, vpn:// payload, AWG-конфиг или JSON.",
        )

        layout.addWidget(search_card)
        layout.addWidget(action_bar)
        layout.addWidget(best_card)
        layout.addWidget(self.servers_empty_state)
        layout.addWidget(self._table_surface(self.servers_table), 1)
        layout.addWidget(footer)
        return page

    def _build_subscriptions_page(self, item: NavigationItem) -> QWidget:
        page = self._page_shell(item)
        layout = page.layout()
        assert isinstance(layout, QVBoxLayout)
        self._configure_table(
            self.subscriptions_table,
            ("Название", "URL", "Серверов", "Обновлено", "Статус", "Ошибка", "ID"),
        )
        self.subscriptions_table.setColumnHidden(6, True)
        primary_actions = self._action_bar(
            self.subscription_add_button,
            self.subscription_refresh_button,
            self.subscription_refresh_all_button,
        )
        secondary_actions = self._action_bar(
            self.subscription_servers_button,
            self.subscription_rename_button,
            self.subscription_edit_url_button,
            self.subscription_delete_button,
        )
        (
            self.subscriptions_empty_state,
            self.subscriptions_empty_title,
            self.subscriptions_empty_text,
        ) = self._empty_state(
            "Подписок пока нет",
            "Добавьте URL подписки или импортируйте серверы вручную на странице серверов.",
        )
        layout.addWidget(primary_actions)
        layout.addWidget(secondary_actions)
        layout.addWidget(self.subscriptions_empty_state)
        layout.addWidget(self._table_surface(self.subscriptions_table), 1)
        return page

    def _build_settings_page(self, item: NavigationItem) -> QWidget:
        page = self._page_shell(item)
        layout = page.layout()
        assert isinstance(layout, QVBoxLayout)
        box = QFrame(self)
        box.setObjectName("Panel")
        grid = QGridLayout(box)
        grid.setContentsMargins(*tokens.spacing(tokens.SPACE_4, tokens.SPACE_4, tokens.SPACE_4, tokens.SPACE_4))
        grid.setHorizontalSpacing(tokens.SPACE_6)
        grid.setVerticalSpacing(tokens.SPACE_3)
        grid.setColumnStretch(1, 1)
        grid.addWidget(self._panel_title("Текущие настройки"), 0, 0, 1, 2)
        self.settings_mode_combo.addItem("PROXY (браузер и приложения)", "PROXY")
        self.settings_mode_combo.addItem("TUN (игры и весь трафик)", "TUN")
        self.settings_routing_description.setWordWrap(True)
        self.settings_routing_description.setObjectName("PageSubtitle")
        grid.addWidget(self._caption("Режим подключения"), 1, 0)
        grid.addWidget(self.settings_mode_combo, 1, 1)
        grid.addWidget(self._caption("Локальные proxy порты"), 2, 0)
        grid.addWidget(self.settings_proxy_ports_value, 2, 1)
        grid.addWidget(self._caption("Системный proxy"), 3, 0)
        grid.addWidget(self.settings_system_proxy_checkbox, 3, 1)
        grid.addWidget(self._caption("Автообновление"), 4, 0)
        grid.addWidget(self.settings_auto_update_checkbox, 4, 1)
        grid.addWidget(self._caption("Профиль маршрутизации"), 5, 0)
        routing_layout = QHBoxLayout()
        routing_layout.setContentsMargins(*tokens.spacing(tokens.SPACE_0, tokens.SPACE_0, tokens.SPACE_0, tokens.SPACE_0))
        routing_layout.setSpacing(tokens.SPACE_3)
        routing_layout.addWidget(self.settings_routing_combo, 1)
        routing_layout.addWidget(self.settings_profile_details_button)
        grid.addLayout(routing_layout, 5, 1)
        grid.addWidget(self._caption("Описание"), 6, 0)
        grid.addWidget(self.settings_routing_description, 6, 1)
        layout.addWidget(box)
        layout.addWidget(self.settings_save_button, 0, Qt.AlignmentFlag.AlignLeft)
        layout.addStretch(1)
        return page

    def _build_components_page(self, item: NavigationItem) -> QWidget:
        page = self._page_shell(item)
        layout = page.layout()
        assert isinstance(layout, QVBoxLayout)
        self._configure_table(
            self.components_table,
            ("Компонент", "Состояние", "Детали", "Ключ"),
        )
        self.components_table.setColumnHidden(3, True)
        component_actions = self._action_bar(self.component_update_button, self.component_update_all_button)

        (
            self.components_empty_state,
            self.components_empty_title,
            self.components_empty_text,
        ) = self._empty_state(
            "Список компонентов пуст",
            "Компоненты runtime появятся здесь после обновления состояния приложения.",
        )

        update_box = QFrame(self)
        update_box.setObjectName("Panel")
        update_layout = QGridLayout(update_box)
        update_layout.setContentsMargins(*tokens.spacing(tokens.SPACE_4, tokens.SPACE_4, tokens.SPACE_4, tokens.SPACE_4))
        update_layout.setHorizontalSpacing(tokens.SPACE_6)
        update_layout.setVerticalSpacing(tokens.SPACE_3)
        update_layout.setColumnStretch(1, 1)
        update_layout.addWidget(self._panel_title("Обновление приложения"), 0, 0, 1, 2)
        self.app_update_status_value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        update_layout.addWidget(self._caption("Статус"), 1, 0)
        update_layout.addWidget(self.app_update_status_value, 1, 1)
        update_buttons = QGridLayout()
        update_buttons.setContentsMargins(*tokens.spacing(tokens.SPACE_0, tokens.SPACE_0, tokens.SPACE_0, tokens.SPACE_0))
        update_buttons.setHorizontalSpacing(tokens.SPACE_3)
        update_buttons.setVerticalSpacing(tokens.SPACE_3)
        update_buttons.addWidget(self.app_update_check_button, 0, 0, 1, 2)
        update_buttons.addWidget(self.app_update_open_button, 1, 0)
        update_buttons.addWidget(self.app_self_update_button, 1, 1)
        update_buttons.setColumnStretch(1, 1)
        update_layout.addLayout(update_buttons, 2, 0, 1, 2)

        layout.addWidget(component_actions)
        layout.addWidget(self.components_empty_state)
        layout.addWidget(self._table_surface(self.components_table), 1)
        layout.addWidget(update_box)
        return page

    def _build_status_page(self, item: NavigationItem) -> QWidget:
        page = self._page_shell(item)
        layout = page.layout()
        assert isinstance(layout, QVBoxLayout)
        self._configure_table(self.status_details_table, ("Параметр", "Значение"))
        layout.addWidget(self.status_healthcheck_button, 0, Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(self._table_surface(self.status_details_table), 1)
        return page

    def _page_shell(self, item: NavigationItem) -> QWidget:
        page = QWidget(self)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(*tokens.spacing(tokens.SPACE_0, tokens.SPACE_0, tokens.SPACE_0, tokens.SPACE_0))
        layout.setSpacing(tokens.SPACE_3)
        title = QLabel(item.title)
        title.setObjectName("PageTitle")
        title.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        subtitle = QLabel(item.subtitle)
        subtitle.setObjectName("PageSubtitle")
        subtitle.setWordWrap(True)
        subtitle.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        layout.addWidget(title)
        layout.addWidget(subtitle)
        return page

    @staticmethod
    def _caption(text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("FieldCaption")
        label.setWordWrap(True)
        return label

    @staticmethod
    def _panel_title(text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("PanelTitle")
        return label

    def _add_metric(self, layout: QGridLayout, row: int, column: int, title: str, value: QLabel) -> None:
        caption = self._caption(title)
        value.setObjectName("FieldValue")
        value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(caption, row, column)
        layout.addWidget(value, row, column + 1)

    def _add_hero_metric(
        self,
        layout: QHBoxLayout,
        icon_name: str,
        title: str,
        value: QLabel,
        color: str,
        *,
        add_separator: bool = True,
    ) -> None:
        item = QWidget(self)
        item.setObjectName("HeroMetric")
        item_layout = QHBoxLayout(item)
        item_layout.setContentsMargins(*tokens.spacing(tokens.SPACE_0, tokens.SPACE_0, tokens.SPACE_0, tokens.SPACE_0))
        item_layout.setSpacing(tokens.SPACE_3)

        icon_label = QLabel()
        icon_label.setObjectName("HeroMetricIcon")
        icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon_label.setFixedSize(tokens.HERO_METRIC_ICON_SIZE, tokens.HERO_METRIC_ICON_SIZE)
        icon_label.setPixmap(
            self._line_icon(icon_name, color, tokens.HERO_METRIC_ICON_SIZE).pixmap(
                tokens.HERO_METRIC_ICON_SIZE,
                tokens.HERO_METRIC_ICON_SIZE,
            )
        )

        text_layout = QVBoxLayout()
        text_layout.setContentsMargins(*tokens.spacing(tokens.SPACE_0, tokens.SPACE_0, tokens.SPACE_0, tokens.SPACE_0))
        text_layout.setSpacing(tokens.SPACE_1)
        caption = self._caption(title)
        caption.setObjectName("HeroMetricCaption")
        value.setObjectName("HeroMetricValue")
        value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        text_layout.addWidget(caption)
        text_layout.addWidget(value)

        item_layout.addWidget(icon_label)
        item_layout.addLayout(text_layout, 1)
        layout.addWidget(item, 1)
        if add_separator:
            separator = QFrame(self)
            separator.setObjectName("HeroMetricDivider")
            separator.setFrameShape(QFrame.Shape.VLine)
            separator.setFixedWidth(tokens.HAIRLINE)
            layout.addWidget(separator)

    def _hero_separator(self) -> QFrame:
        separator = QFrame(self)
        separator.setObjectName("HeroSeparator")
        separator.setFrameShape(QFrame.Shape.HLine)
        separator.setFixedHeight(tokens.HAIRLINE)
        return separator

    def _toolbar(self) -> QFrame:
        frame = QFrame(self)
        frame.setObjectName("Toolbar")
        frame.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        layout = QHBoxLayout(frame)
        layout.setContentsMargins(*tokens.spacing(tokens.SPACE_0, tokens.SPACE_0, tokens.SPACE_0, tokens.SPACE_0))
        layout.setSpacing(tokens.SPACE_3)
        return frame

    def _action_bar(self, *buttons: QPushButton) -> QFrame:
        frame = self._toolbar()
        layout = frame.layout()
        assert isinstance(layout, QHBoxLayout)
        for button in buttons:
            layout.addWidget(button)
        layout.addStretch(1)
        return frame

    def _empty_state(self, title: str, text: str) -> tuple[QFrame, QLabel, QLabel]:
        frame = QFrame(self)
        frame.setObjectName("EmptyState")
        frame.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(*tokens.spacing(tokens.SPACE_5, tokens.SPACE_4, tokens.SPACE_5, tokens.SPACE_4))
        layout.setSpacing(tokens.SPACE_2)
        title_label = QLabel(title)
        title_label.setObjectName("EmptyTitle")
        text_label = QLabel(text)
        text_label.setObjectName("EmptyText")
        text_label.setWordWrap(True)
        layout.addWidget(title_label)
        layout.addWidget(text_label)
        frame.hide()
        return frame, title_label, text_label

    def _table_surface(self, table: QTableWidget) -> QFrame:
        frame = QFrame(self)
        frame.setObjectName("TableSurface")
        frame.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(*tokens.spacing(tokens.SPACE_0, tokens.SPACE_0, tokens.SPACE_0, tokens.SPACE_0))
        layout.setSpacing(tokens.SPACE_0)
        layout.addWidget(table)
        return frame

    @staticmethod
    def _table_container(table: QTableWidget) -> QWidget:
        parent = table.parentWidget()
        if isinstance(parent, QFrame) and parent.objectName() == "TableSurface":
            return parent
        return table

    @staticmethod
    def _update_empty_state(
        frame: QFrame | None,
        title_label: QLabel | None,
        text_label: QLabel | None,
        table: QTableWidget,
        *,
        visible: bool,
        title: str | None = None,
        text: str | None = None,
    ) -> None:
        table_container = MainWindow._table_container(table)
        if frame is None or title_label is None or text_label is None:
            table_container.setVisible(not visible)
            return
        if title is not None:
            title_label.setText(title)
        if text is not None:
            text_label.setText(text)
        frame.setVisible(visible)
        table_container.setVisible(not visible)

    @staticmethod
    def _configure_table(table: QTableWidget, headers: tuple[str, ...]) -> None:
        table.setColumnCount(len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.verticalHeader().setVisible(False)
        table.verticalHeader().setDefaultSectionSize(tokens.TABLE_ROW_HEIGHT)
        table.setAlternatingRowColors(True)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        table.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        table.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        table.setTextElideMode(Qt.TextElideMode.ElideRight)
        table.setWordWrap(False)
        table.setShowGrid(False)
        table.setCornerButtonEnabled(False)
        table.setFrameShape(QFrame.Shape.NoFrame)
        table.setLineWidth(0)
        table.setMidLineWidth(0)
        table.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        header = table.horizontalHeader()
        header.setStretchLastSection(True)
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        header.setHighlightSections(False)
        header.setMinimumSectionSize(tokens.SPACE_6 * 3)

    def refresh_data(self) -> None:
        try:
            self._state = self.service.get_current_state()
            self._servers = self.service.list_servers(sorted_by_name=True)
            self._subscriptions = self.service.list_subscriptions()
            self._settings = self.service.get_settings(validated=False)
            self._routing_profiles = self.service.list_routing_profiles()
            components = self.service.get_components_status()
        except Exception as exc:  # noqa: BLE001
            show_error_dialog(self, "Ошибка обновления данных", str(exc))
            return

        self._update_server_selector()
        self._update_connection_summary()
        self._update_servers_table()
        self._update_subscriptions_table()
        self._update_settings_page()
        self._update_components_table(components)
        self._update_app_update_summary()
        self._update_status_details()
        self._update_tray_actions()

    def _update_connection_summary(self) -> None:
        server = self._active_server()
        selected_server = self._selected_connection_server()
        running = self._state.is_running
        mode = self._state.mode or self._settings.connection_mode
        self.status_value.setText("Подключено" if running else "Не подключено")
        self.active_server_value.setText(server.name if server else "-")
        self.mode_value.setText(str(mode or "-").upper())
        self.backend_value.setText(self._backend_label(self._state.backend_id if running else None))
        self.routing_value.setText(self._state.routing_profile_name or self._active_routing_profile_label())
        self.pid_value.setText(str(self._state.pid) if self._state.pid else "-")
        self.connect_button.setText("Отключиться" if running else "Подключиться")
        self.connect_button.setIcon(
            self.style().standardIcon(
                QStyle.StandardPixmap.SP_MediaStop if running else QStyle.StandardPixmap.SP_MediaPlay
            )
        )
        can_toggle_connection = self._active_worker is None and (running or selected_server is not None)
        self.connect_button.setEnabled(can_toggle_connection)
        self.connect_button.setToolTip("" if can_toggle_connection else "Выберите сервер из списка")
        controls_enabled = self._active_worker is None and not running
        self.server_selector.setEnabled(controls_enabled)
        self.connection_server_filter.setEnabled(controls_enabled)
        self.connection_subscription_filter.setEnabled(controls_enabled)
        self.connection_best_server_button.setEnabled(controls_enabled and bool(self._servers))
        self.connection_ping_button.setEnabled(self._active_worker is None and bool(self._servers))

        badge_text, badge_detail, badge_state = self._connection_status_presentation(running, server)
        self.connection_status_badge.setText(badge_text)
        self.connection_status_detail.setText(badge_detail)
        self.connection_status_detail.setVisible(bool(badge_detail))
        self._set_widget_state(self.connection_status_badge, badge_state)
        self.connection_status_orb.set_state(badge_state)
        self._update_connection_server_preview()
        self._refresh_connection_card_states()

    def _update_server_selector(self, *_args: object) -> None:
        self._update_connection_subscription_filter()
        current_id = self._connection_selected_server_id or self.server_selector.currentData()
        candidates = self._connection_server_candidates()
        if self._state.is_running and self._state.server_id:
            target_id = self._state.server_id
        elif current_id and any(server.id == current_id for server in candidates):
            target_id = str(current_id)
        else:
            target_id = None
        self._connection_selected_server_id = target_id

        self.server_selector.blockSignals(True)
        self.server_selector.clear()
        for server in candidates:
            self.server_selector.addItem(self._server_selector_label(server), server.id)

        if not candidates:
            placeholder = "Серверов пока нет" if not self._servers else "По фильтру ничего не найдено"
            self.server_selector.addItem(placeholder, None)

        if target_id is not None:
            index = self.server_selector.findData(target_id)
            if index >= 0:
                self.server_selector.setCurrentIndex(index)
        elif candidates:
            self.server_selector.setCurrentIndex(-1)
        self.server_selector.blockSignals(False)
        self._rebuild_connection_server_cards(candidates)
        self._update_connection_server_preview()
        self._update_connection_summary()

    def _connection_status_presentation(
        self,
        running: bool,
        server: ServerEntry | None,
    ) -> tuple[str, str, str]:
        if self._active_worker is not None:
            if self._active_operation_title == "Подключение":
                title = "Подключается"
            elif self._active_operation_title == "Отключение":
                title = "Отключается"
            else:
                title = "Выполняется"
            return title, self._active_operation_message or "Операция выполняется...", "busy"
        if self._last_operation_error:
            return "Ошибка", self._last_operation_error, "error"
        if running:
            server_name = server.name if server is not None else "сервер"
            return "Подключено", f"VPN-соединение активно: {server_name}", "connected"
        return "Не подключено", "VPN-соединение не активно", "disconnected"

    def _update_connection_server_preview(self, *_args: object) -> None:
        server = self._selected_connection_server()
        active_server = self._active_server()
        preview_server = active_server if self._state.is_running and active_server is not None else server
        if preview_server is None:
            self.connection_selected_server_value.setText("Сервер не выбран")
            self.connection_selected_server_value.setToolTip("")
            self.connection_selected_meta_value.setText("-")
            self.connection_selected_meta_value.setToolTip("")
            self.connection_selected_ping_value.setText("-")
            self.connection_selected_ping_preview_value.setText("-")
            if self.connection_server_preview is not None:
                self.connection_server_preview.hide()
            return
        if self.connection_server_preview is not None:
            self.connection_server_preview.show()
        ping = self._tcp_ping_label(preview_server)
        meta = f"{preview_server.protocol.upper()} {preview_server.host}:{preview_server.port}"
        self.connection_selected_server_value.setText(self._compact_text(preview_server.name, 58))
        self.connection_selected_server_value.setToolTip(preview_server.name)
        self.connection_selected_meta_value.setText(self._compact_text(meta, 72))
        self.connection_selected_meta_value.setToolTip(meta)
        self.connection_selected_ping_value.setText(ping)
        self.connection_selected_ping_preview_value.setText(ping)

    def _connection_server_candidates(self) -> list[ServerEntry]:
        query = self.connection_server_filter.text().strip().lower()
        subscription_id = self.connection_subscription_filter.currentData()
        servers = self._sort_servers_by_cached_ping(list(self._servers))
        if subscription_id:
            servers = [server for server in servers if server.subscription_id == subscription_id]
        if not query:
            return servers
        return [server for server in servers if self._server_matches_query(server, query)]

    def _selected_connection_server(self) -> ServerEntry | None:
        server_id = self._connection_selected_server_id or self.server_selector.currentData()
        if not server_id:
            return None
        return next((server for server in self._servers if server.id == server_id), None)

    def _select_best_connection_server(self) -> None:
        try:
            best = self.service.best_tcp_ping_server(self._servers)
        except Exception:
            best = None
        if best is None:
            return
        self.connection_server_filter.clear()
        self.connection_subscription_filter.setCurrentIndex(0)
        self._select_connection_server(best.id)

    def _select_connection_server(self, server_id: str) -> None:
        if self._state.is_running or self._active_worker is not None:
            return
        if not any(server.id == server_id for server in self._servers):
            return
        self._connection_selected_server_id = server_id
        index = self.server_selector.findData(server_id)
        if index >= 0:
            self.server_selector.setCurrentIndex(index)
        self._update_connection_summary()

    def _update_connection_subscription_filter(self) -> None:
        current_id = self.connection_subscription_filter.currentData()
        self.connection_subscription_filter.blockSignals(True)
        self.connection_subscription_filter.clear()
        self.connection_subscription_filter.addItem("Все подписки", None)
        for subscription in sorted(self._subscriptions, key=lambda item: item.title.lower()):
            self.connection_subscription_filter.addItem(subscription.title, subscription.id)
        if current_id:
            index = self.connection_subscription_filter.findData(current_id)
            if index >= 0:
                self.connection_subscription_filter.setCurrentIndex(index)
        self.connection_subscription_filter.blockSignals(False)

    def _rebuild_connection_server_cards(self, servers: list[ServerEntry]) -> None:
        layout = self.connection_server_list_layout
        if layout is None:
            return
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._connection_server_cards.clear()

        for server in servers:
            card = self._connection_server_card(server)
            card.clicked.connect(self._select_connection_server)
            self._connection_server_cards[server.id] = card
            layout.addWidget(card)

        if not servers:
            empty = QLabel("Серверов пока нет" if not self._servers else "По фильтру ничего не найдено")
            empty.setObjectName("ConnectionServerEmpty")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty.setMinimumHeight(tokens.SPACE_4 * 4)
            layout.addWidget(empty)
        layout.addStretch(1)
        self.connection_server_count_label.setText(self._server_count_label(len(servers)))
        self._refresh_connection_card_states()

    def _connection_server_card(self, server: ServerEntry) -> ServerSelectionCard:
        card = ServerSelectionCard(server.id, self)
        layout = QHBoxLayout(card)
        layout.setContentsMargins(*tokens.spacing(tokens.SPACE_3, tokens.SPACE_3, tokens.SPACE_3, tokens.SPACE_3))
        layout.setSpacing(tokens.SPACE_3)

        icon = QLabel(server.protocol[:1].upper() or "?")
        icon.setObjectName("ServerCardIcon")
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon.setFixedSize(tokens.ICON_SIZE_XL, tokens.ICON_SIZE_XL)

        text_layout = QVBoxLayout()
        text_layout.setContentsMargins(*tokens.spacing(tokens.SPACE_0, tokens.SPACE_0, tokens.SPACE_0, tokens.SPACE_0))
        text_layout.setSpacing(tokens.SPACE_1)
        title = QLabel(self._compact_text(server.name, 54))
        title.setObjectName("ServerCardTitle")
        title.setToolTip(server.name)
        title.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        meta = QLabel(f"{server.protocol.upper()} • {server.host}:{server.port}")
        meta.setObjectName("ServerCardMeta")
        meta.setToolTip(meta.text())
        meta.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        text_layout.addWidget(title)
        text_layout.addWidget(meta)

        ping = QLabel(self._tcp_ping_label(server))
        ping.setObjectName("ServerCardPing")
        ping.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._set_widget_state(ping, "ok" if ping.text().endswith("ms") else "unknown")

        selected = QLabel("Выбран ✓")
        selected.setObjectName("ServerCardSelected")
        selected.setAlignment(Qt.AlignmentFlag.AlignCenter)

        layout.addWidget(icon)
        layout.addLayout(text_layout, 1)
        layout.addWidget(ping)
        layout.addWidget(selected)
        for child in (icon, title, meta, ping, selected):
            child.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        return card

    def _refresh_connection_card_states(self) -> None:
        selected_id = self._state.server_id if self._state.is_running else self._connection_selected_server_id
        controls_enabled = self._active_worker is None and not self._state.is_running
        for server_id, card in self._connection_server_cards.items():
            selected = server_id == selected_id
            card.setEnabled(controls_enabled or selected)
            self._set_widget_state(card, "selected" if selected else "idle")
            for child in card.findChildren(QLabel):
                if child.objectName() == "ServerCardSelected":
                    child.setVisible(selected)

    @staticmethod
    def _server_count_label(count: int) -> str:
        if count % 10 == 1 and count % 100 != 11:
            word = "сервер"
        elif count % 10 in {2, 3, 4} and count % 100 not in {12, 13, 14}:
            word = "сервера"
        else:
            word = "серверов"
        verb = "Показан" if count == 1 else "Показано"
        return f"{verb} {count} {word}"

    @staticmethod
    def _server_selector_label(server: ServerEntry) -> str:
        return f"{server.name} · {server.protocol.upper()} · {server.host}:{server.port}"

    @staticmethod
    def _compact_text(value: str, max_length: int) -> str:
        text = str(value)
        if len(text) <= max_length:
            return text
        if max_length <= 3:
            return text[:max_length]
        return f"{text[: max_length - 3]}..."

    def _set_widget_state(self, widget: QWidget, state: str) -> None:
        widget.setProperty("state", state)
        widget.style().unpolish(widget)
        widget.style().polish(widget)
        widget.update()

    def _update_servers_table(self) -> None:
        selected_before = self._selected_server()
        selected_before_id = selected_before.id if selected_before is not None else None
        self._visible_servers = self._filtered_servers()
        self._visible_servers = self._sort_servers_by_cached_ping(self._visible_servers)
        self.servers_table.setRowCount(len(self._visible_servers))
        active_server_id = self._state.server_id if self._state.is_running else None
        for row, server in enumerate(self._visible_servers):
            self._set_row(
                self.servers_table,
                row,
                (
                    "",
                    "☆",
                    server.name,
                    server.protocol.upper(),
                    server.host,
                    str(server.port),
                    self._server_source_label(server),
                    self._tcp_ping_label(server),
                    "●" if server.id == active_server_id else "●",
                    "",
                    server.id,
                ),
            )
            self._format_servers_table_row(row, server, active_server_id)
        header = self.servers_table.horizontalHeader()
        header.resizeSection(0, tokens.SERVER_TABLE_CHECK_COLUMN_WIDTH)
        header.resizeSection(1, tokens.PAGER_SIZE)
        header.resizeSection(3, tokens.SERVER_TABLE_PROTOCOL_COLUMN_WIDTH)
        header.resizeSection(SERVER_TABLE_ACTIONS_COLUMN, tokens.SPACE_4 * 4)
        self.servers_table.setColumnHidden(SERVER_TABLE_ID_COLUMN, True)
        if not self._visible_servers:
            query = self.servers_filter.text().strip()
            source = self.servers_source_filter.currentData()
            if (query or source in {"favorite", "manual", "subscription"}) and self._servers:
                self._update_empty_state(
                    self.servers_empty_state,
                    self.servers_empty_title,
                    self.servers_empty_text,
                    self.servers_table,
                    visible=True,
                    title="Ничего не найдено",
                    text="Измените поисковый запрос, очистите фильтр или добавьте сервер в избранное.",
                )
            else:
                self._update_empty_state(
                    self.servers_empty_state,
                    self.servers_empty_title,
                    self.servers_empty_text,
                    self.servers_table,
                    visible=True,
                    title="Серверов пока нет",
                    text="Добавьте сервер вручную или импортируйте ссылку, подписку, vpn:// payload, AWG-конфиг или JSON.",
                )
        else:
            self._update_empty_state(
                self.servers_empty_state,
                self.servers_empty_title,
                self.servers_empty_text,
                self.servers_table,
                visible=False,
            )
            if self.servers_table.selectedItems():
                self._refresh_servers_selection_marker()
            elif selected_before_id and any(server.id == selected_before_id for server in self._visible_servers):
                self._select_server_row(selected_before_id)
            else:
                self.servers_table.selectRow(0)
        self._update_servers_footer_label()
        self._update_best_server_label()
        self._update_server_action_state()

    def _update_subscriptions_table(self) -> None:
        self.subscriptions_table.setRowCount(len(self._subscriptions))
        for row, subscription in enumerate(self._subscriptions):
            status = "Нужна проверка" if subscription.last_error else "Готова"
            self._set_row(
                self.subscriptions_table,
                row,
                (
                    subscription.title,
                    subscription.url,
                    str(len(subscription.server_ids)),
                    self._short_datetime(subscription.updated_at),
                    status,
                    subscription.last_error or "-",
                    subscription.id,
                ),
            )
            if subscription.last_error:
                self._set_cell_foreground(self.subscriptions_table, row, 4, tokens.COLOR_WARNING)
                self._set_cell_foreground(self.subscriptions_table, row, 5, tokens.COLOR_DANGER)
            else:
                self._set_cell_foreground(self.subscriptions_table, row, 4, tokens.COLOR_SUCCESS)
        self.subscriptions_table.resizeColumnsToContents()
        self.subscriptions_table.setColumnHidden(6, True)
        self._update_empty_state(
            self.subscriptions_empty_state,
            self.subscriptions_empty_title,
            self.subscriptions_empty_text,
            self.subscriptions_table,
            visible=not self._subscriptions,
            title="Подписок пока нет",
            text="Добавьте URL подписки или импортируйте серверы вручную на странице серверов.",
        )
        self._update_subscription_action_state()

    def _update_settings_page(self) -> None:
        mode = str(self._settings.connection_mode or "PROXY").upper()
        mode_index = self.settings_mode_combo.findData(mode)
        if mode_index >= 0:
            self.settings_mode_combo.setCurrentIndex(mode_index)
        self.settings_system_proxy_checkbox.setChecked(bool(self._settings.set_system_proxy))
        self.settings_auto_update_checkbox.setChecked(bool(self._settings.auto_update_subscriptions_on_startup))

        current_profile_id = self.settings_routing_combo.currentData()
        self.settings_routing_combo.blockSignals(True)
        self.settings_routing_combo.clear()
        for profile in self._routing_profiles:
            self.settings_routing_combo.addItem(profile.name, profile.profile_id)
        active_index = self.settings_routing_combo.findData(self._settings.active_routing_profile_id)
        if active_index < 0 and current_profile_id is not None:
            active_index = self.settings_routing_combo.findData(current_profile_id)
        if active_index >= 0:
            self.settings_routing_combo.setCurrentIndex(active_index)
        self.settings_routing_combo.blockSignals(False)
        self._update_selected_routing_profile_description()

    def _update_components_table(self, components) -> None:
        self.components_table.setRowCount(len(components.items))
        for row, component in enumerate(components.items):
            self._set_row(
                self.components_table,
                row,
                (
                    component.title,
                    "Установлен" if component.installed else "Отсутствует",
                    component.detail or "-",
                    component.key,
                ),
            )
            if component.installed:
                self._set_cell_foreground(self.components_table, row, 1, tokens.COLOR_SUCCESS)
            else:
                self._set_cell_foreground(self.components_table, row, 1, tokens.COLOR_WARNING)
        self.components_table.resizeColumnsToContents()
        self.components_table.setColumnHidden(3, True)
        self._update_empty_state(
            self.components_empty_state,
            self.components_empty_title,
            self.components_empty_text,
            self.components_table,
            visible=not components.items,
            title="Список компонентов пуст",
            text="Компоненты runtime появятся здесь после обновления состояния приложения.",
        )
        self._update_component_action_state()

    def _update_app_update_summary(self) -> None:
        release = self.service.available_app_update()
        if release is not None and release.latest_version:
            self.app_update_status_value.setText(f"Доступна версия {release.latest_version}")
            self.app_self_update_button.setEnabled(self.service.can_self_update())
            return
        cached = self.service.get_cached_app_update()
        if cached is not None and cached.error:
            self.app_update_status_value.setText(f"Ошибка проверки: {cached.error}")
        elif cached is not None and cached.latest_version:
            self.app_update_status_value.setText(f"Обновлений нет. Latest: {cached.latest_version}")
        else:
            self.app_update_status_value.setText("Проверка еще не выполнялась")
        self.app_self_update_button.setEnabled(False)

    def _update_status_details(self) -> None:
        try:
            status = self.service.get_runtime_status()
        except Exception:
            status = None
        health = status.healthcheck_result if status is not None else None
        server = status.active_server if status is not None else self._active_server()
        rows = (
            ("Версия", f"v{APP_VERSION}"),
            ("Статус", self.status_value.text()),
            ("Runtime state", "running" if self._state.is_running else "stopped"),
            ("Process state", status.process_state if status is not None else "-"),
            ("System proxy state", status.system_proxy_state if status is not None else "-"),
            ("Активный backend", status.backend_title if status is not None else self.backend_value.text()),
            ("Backend ID", status.backend_id if status is not None else "-"),
            ("PID", str(status.pid) if status is not None and status.pid else self.pid_value.text()),
            ("Активный сервер", server.name if server is not None else "-"),
            ("Адрес сервера", f"{server.host}:{server.port}" if server is not None else "-"),
            ("Routing profile", status.routing_profile if status is not None else self.routing_value.text()),
            ("Health-check", self._healthcheck_label(health)),
            ("TUN интерфейс", self._state.tun_interface_name or "-"),
            ("TUN IPv4", self._state.tun_interface_ipv4 or "-"),
        )
        self.status_details_table.setRowCount(len(rows))
        for row, values in enumerate(rows):
            self._set_row(self.status_details_table, row, values)
        self.status_details_table.resizeColumnsToContents()

    @staticmethod
    def _set_row(table: QTableWidget, row: int, values: tuple[str, ...]) -> None:
        for column, value in enumerate(values):
            item = QTableWidgetItem(str(value))
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            item.setToolTip(str(value))
            table.setItem(row, column, item)

    @staticmethod
    def _set_cell_foreground(table: QTableWidget, row: int, column: int, color: str) -> None:
        item = table.item(row, column)
        if item is not None:
            item.setForeground(QBrush(QColor(color)))

    def _format_servers_table_row(
        self,
        row: int,
        server: ServerEntry,
        active_server_id: str | None,
    ) -> None:
        for column in (0, 1, 5, 8, SERVER_TABLE_ACTIONS_COLUMN):
            item = self.servers_table.item(row, column)
            if item is not None:
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        for column in (3, SERVER_TABLE_ACTIONS_COLUMN):
            self.servers_table.removeCellWidget(row, column)

        selected_marker = self.servers_table.item(row, 0)
        favorite_marker = self.servers_table.item(row, 1)
        if selected_marker is not None:
            selected_marker.setText("")
            selected_marker.setFlags(selected_marker.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            selected_marker.setCheckState(Qt.CheckState.Unchecked)
            selected_marker.setToolTip("Выбранный сервер")
        if favorite_marker is not None:
            is_favorite = self._is_favorite_server(server)
            favorite_marker.setText("★" if is_favorite else "☆")
            favorite_marker.setForeground(
                QBrush(QColor(tokens.COLOR_WARNING if is_favorite else tokens.COLOR_TEXT_DISABLED))
            )
            favorite_marker.setToolTip("Убрать из избранного" if is_favorite else "Добавить в избранное")
        protocol_state = self._server_protocol_state(server)
        protocol_item = self.servers_table.item(row, 3)
        if protocol_item is not None:
            protocol_item.setText("")
            protocol_item.setToolTip(server.protocol.upper())
        self.servers_table.setCellWidget(
            row,
            3,
            self._styled_table_label(
                server.protocol.upper(),
                "ProtocolBadge",
                protocol_state,
                tooltip=server.protocol.upper(),
            ),
        )
        for column in (4, 5):
            item = self.servers_table.item(row, column)
            if item is not None:
                item.setForeground(QBrush(QColor(tokens.COLOR_TEXT_MUTED)))
        source_item = self.servers_table.item(row, 6)
        if source_item is not None:
            source_item.setForeground(QBrush(QColor(tokens.COLOR_TEXT_SECONDARY)))

        ping = self._tcp_ping_label(server)
        ping_state = self._server_ping_state(ping)
        ping_item = self.servers_table.item(row, 7)
        if ping_item is not None:
            ping_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            ping_item.setForeground(QBrush(QColor(self._server_ping_color(ping_state))))
        _status_label, status_state = self._server_status_presentation(server, active_server_id, ping_state)
        status_item = self.servers_table.item(row, 8)
        if status_item is not None:
            status_item.setText("●")
            status_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            status_item.setForeground(QBrush(QColor(self._server_status_color(status_state))))
            status_item.setToolTip(_status_label)
        self.servers_table.setCellWidget(row, SERVER_TABLE_ACTIONS_COLUMN, self._server_actions_button(server))

    def _styled_table_label(
        self,
        text: str,
        object_name: str,
        state: str,
        *,
        tooltip: str | None = None,
    ) -> QWidget:
        cell = QWidget(self.servers_table)
        cell.setObjectName("Transparent")
        cell.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        layout = QHBoxLayout(cell)
        layout.setContentsMargins(*tokens.spacing(tokens.SPACE_2, tokens.SPACE_0, tokens.SPACE_2, tokens.SPACE_0))
        layout.setSpacing(tokens.SPACE_0)

        label = QLabel(text)
        label.setObjectName(object_name)
        label.setProperty("state", state)
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setToolTip(tooltip if tooltip is not None else text)
        label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        label.setMinimumWidth(tokens.SERVER_TABLE_PROTOCOL_BADGE_MIN_WIDTH)
        label.setFixedHeight(tokens.SERVER_TABLE_PROTOCOL_BADGE_HEIGHT)
        label.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        layout.addWidget(label, 0, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        return cell

    def _server_actions_button(self, server: ServerEntry) -> QPushButton:
        button = QPushButton("⋯", self.servers_table)
        button.setObjectName("TableActionButton")
        button.setToolTip("Действия")
        button.setFixedSize(tokens.CONTROL_HEIGHT_COMPACT, tokens.CONTROL_HEIGHT_COMPACT)
        button.setEnabled(self._active_worker is None)
        button.clicked.connect(lambda _checked=False, server_id=server.id, source=button: self._show_server_row_menu(server_id, source))
        return button

    def _show_server_table_context_menu(self, point) -> None:
        row = self.servers_table.rowAt(point.y())
        if row < 0:
            return
        id_item = self.servers_table.item(row, SERVER_TABLE_ID_COLUMN)
        if id_item is None:
            return
        self._show_server_row_menu(id_item.text(), self.servers_table.viewport(), point)

    def _show_server_row_menu(self, server_id: str, source: QWidget, point=None) -> None:
        if self._active_worker is not None:
            return
        self._select_server_row(server_id)
        server = self._selected_server()
        if server is None:
            return

        menu = QMenu(self)
        details_action = menu.addAction("Детали")
        details_action.triggered.connect(self._show_selected_server_details)

        rename_action = menu.addAction("Переименовать")
        rename_action.triggered.connect(self._rename_selected_server)

        edit_link_action = menu.addAction("Изменить ссылку")
        edit_link_action.setEnabled(server.source == "manual" and not server.is_amneziawg)
        edit_link_action.triggered.connect(self._edit_selected_server_link)

        detach_action = menu.addAction("Отвязать")
        detach_action.setEnabled(server.source == "subscription")
        detach_action.triggered.connect(self._detach_selected_server)

        menu.addSeparator()
        delete_action = menu.addAction("Удалить")
        delete_action.triggered.connect(self._delete_selected_server)

        if point is None:
            menu.exec(source.mapToGlobal(source.rect().bottomLeft()))
        else:
            menu.exec(source.mapToGlobal(point))

    @staticmethod
    def _server_protocol_state(server: ServerEntry) -> str:
        protocol = server.protocol.lower()
        if protocol in {"vless", "vmess", "trojan", "amneziawg"}:
            return protocol
        return "other"

    @staticmethod
    def _server_ping_state(ping: str) -> str:
        if ping.endswith("ms"):
            try:
                latency = int(ping.split()[0])
            except (ValueError, IndexError):
                latency = 0
            return "slow" if latency > 80 else "ok"
        if ping == "UDP-only":
            return "udp"
        if ping == "-":
            return "unknown"
        return "error"

    @staticmethod
    def _server_ping_color(state: str) -> str:
        return {
            "ok": tokens.COLOR_SUCCESS,
            "slow": tokens.COLOR_WARNING,
            "udp": tokens.COLOR_INFO,
            "error": tokens.COLOR_DANGER,
            "unknown": tokens.COLOR_TEXT_MUTED,
        }.get(state, tokens.COLOR_TEXT_MUTED)

    @staticmethod
    def _server_status_color(state: str) -> str:
        return {
            "active": tokens.COLOR_SUCCESS,
            "ok": tokens.COLOR_SUCCESS,
            "slow": tokens.COLOR_WARNING,
            "udp": tokens.COLOR_INFO,
            "error": tokens.COLOR_DANGER,
            "unknown": tokens.COLOR_TEXT_MUTED,
        }.get(state, tokens.COLOR_TEXT_MUTED)

    @staticmethod
    def _server_status_presentation(
        server: ServerEntry,
        active_server_id: str | None,
        ping_state: str,
    ) -> tuple[str, str]:
        if server.id == active_server_id:
            return "Активен", "active"
        if ping_state == "ok":
            return "Готов", "ok"
        if ping_state == "slow":
            return "Медленно", "slow"
        if ping_state == "udp":
            return "UDP", "udp"
        if ping_state == "error":
            return "Ошибка", "error"
        return "Не проверен", "unknown"

    def _refresh_servers_selection_marker(self) -> None:
        selected = self._selected_server()
        selected_id = selected.id if selected is not None else None
        for row in range(self.servers_table.rowCount()):
            id_item = self.servers_table.item(row, SERVER_TABLE_ID_COLUMN)
            marker = self.servers_table.item(row, 0)
            if id_item is not None and marker is not None:
                marker.setCheckState(Qt.CheckState.Checked if id_item.text() == selected_id else Qt.CheckState.Unchecked)

    def _update_servers_footer_label(self) -> None:
        total = len(self._servers)
        visible = len(self._visible_servers)
        if visible:
            self.servers_count_label.setText(f"Показано 1-{visible} из {total} серверов")
        else:
            self.servers_count_label.setText(f"Показано 0 из {total} серверов")

    @staticmethod
    def _is_favorite_server(server: ServerEntry) -> bool:
        return bool(server.extra.get("favorite")) if isinstance(server.extra, dict) else False

    def _toggle_connection(self) -> None:
        if self._active_worker is not None:
            return
        if self._state.is_running:
            self._run_background_operation(
                "Отключение",
                "Остановка подключения...",
                lambda: self.service.disconnect(),
                self._on_disconnect_finished,
            )
            return

        server = self._selected_connection_server()
        if server is None:
            show_warning_dialog(
                self,
                "Подключение",
                "Список серверов пуст. Добавьте сервер или импортируйте ссылку на странице «Серверы».",
            )
            return
        self._connect_server_id(server.id)

    def _connect_server_id(self, server_id: str, *, terminate_winws_conflicts: bool = False) -> None:
        self._pending_connect_server_id = server_id
        self._run_background_operation(
            "Подключение",
            "Подготовка подключения...",
            lambda **kwargs: self.service.connect(
                str(server_id),
                terminate_winws_conflicts=terminate_winws_conflicts,
                **kwargs,
            ),
            self._on_connect_finished,
            progress_kwarg="progress_callback",
        )

    def _run_background_operation(
        self,
        title: str,
        initial_message: str,
        function: Callable[..., object],
        on_finished: Callable[[object], None],
        *,
        progress_kwarg: str | None = None,
    ) -> None:
        self._active_operation_title = title
        self._active_operation_message = initial_message
        self._last_operation_error = None
        self._progress_dialog = QProgressDialog(initial_message, None, 0, 0, self)
        self._progress_dialog.setWindowTitle(title)
        self._progress_dialog.setWindowModality(Qt.WindowModality.ApplicationModal)
        self._progress_dialog.setMinimumDuration(0)
        self._progress_dialog.show()

        worker = FunctionWorker(function, progress_kwarg=progress_kwarg)
        self._active_worker = worker
        self.connect_button.setEnabled(False)
        self.refresh_button.setEnabled(False)
        self._set_server_actions_enabled(False)
        self._set_subscription_actions_enabled(False)
        self._set_component_actions_enabled(False)
        self.status_healthcheck_button.setEnabled(False)
        self._update_connection_summary()
        worker.signals.progress.connect(self._on_operation_progress)
        worker.signals.finished.connect(on_finished)
        worker.signals.finished.connect(self._cleanup_worker)
        worker.signals.failed.connect(self._on_operation_failed)
        worker.signals.failed.connect(lambda _exc: self._cleanup_worker())
        self.thread_pool.start(worker)

    def _on_operation_progress(self, message: str) -> None:
        self._active_operation_message = message
        if self._progress_dialog is not None:
            self._progress_dialog.setLabelText(message)
        self._update_connection_summary()

    def _on_connect_finished(self, result: object) -> None:
        if isinstance(result, ConnectionResult) and result.health_warning:
            show_warning_dialog(self, "Подключение установлено с предупреждением", result.health_warning)
        self.refresh_data()

    def _on_disconnect_finished(self, _result: object) -> None:
        self.refresh_data()

    def _on_operation_failed(self, error: Exception) -> None:
        if isinstance(error, WinwsConflictError) and self._pending_connect_server_id:
            summary = self.service.format_process_conflict_summary(error.conflicts)
            should_terminate = ask_confirmation(
                self,
                "Конфликтующие процессы Winws",
                "Перед подключением нужно остановить конфликтующие процессы:\n"
                f"{summary}\n\nЗавершить их автоматически и повторить подключение?",
                default_yes=False,
            )
            if should_terminate:
                server_id = self._pending_connect_server_id
                QTimer.singleShot(0, lambda: self._connect_server_id(server_id, terminate_winws_conflicts=True))
                return
        self._last_operation_error = str(error)
        show_error_dialog(self, "Ошибка операции", str(error))

    def _cleanup_worker(self) -> None:
        if self._progress_dialog is not None:
            self._progress_dialog.close()
            self._progress_dialog = None
        self._active_worker = None
        self._active_operation_title = ""
        self._active_operation_message = ""
        self.refresh_button.setEnabled(True)
        self._set_server_actions_enabled(True)
        self._set_subscription_actions_enabled(True)
        self._set_component_actions_enabled(True)
        self.status_healthcheck_button.setEnabled(True)
        self.refresh_data()

    def _run_startup_maintenance(self) -> None:
        if self._startup_maintenance_started:
            return
        self._startup_maintenance_started = True
        if not hasattr(self.service, "run_startup_maintenance"):
            return
        if self._active_worker is not None:
            self._startup_maintenance_started = False
            QTimer.singleShot(250, self._run_startup_maintenance)
            return
        self._run_background_operation(
            "Подготовка Vynex",
            "Проверяем обновления и runtime...",
            lambda **kwargs: self.service.run_startup_maintenance(**kwargs),
            self._on_startup_maintenance_finished,
            progress_kwarg="progress_callback",
        )

    def _on_startup_maintenance_finished(self, result: object) -> None:
        messages: list[str] = []
        runtime_update = getattr(result, "runtime_update", None)
        if runtime_update is not None:
            details = ", ".join(getattr(runtime_update, "details", ()) or ()) or "-"
            messages.append(f"Runtime подготовлен: {details}")
            warnings = tuple(getattr(runtime_update, "warnings", ()) or ())
            if warnings:
                messages.append("Предупреждения installer:\n" + "\n".join(warnings))

        subscription_refresh = getattr(result, "subscription_refresh", None)
        if subscription_refresh is not None:
            success = getattr(subscription_refresh, "success", ()) or ()
            failed = getattr(subscription_refresh, "failed", ()) or ()
            if failed:
                details = "\n".join(f"{subscription.title}: {error}" for subscription, error in failed[:5])
                messages.append(f"Авто-обновление подписок: успешно {len(success)}, ошибок {len(failed)}\n{details}")

        app_update = getattr(result, "app_update", None)
        if app_update is not None and getattr(app_update, "is_update_available", False):
            version = getattr(app_update, "latest_version", None) or "-"
            messages.append(f"Доступно обновление приложения: {version}")

        if messages:
            show_info_dialog(self, "Подготовка Vynex", "\n\n".join(messages))
        self.refresh_data()

    def _active_server(self) -> ServerEntry | None:
        if not self._state.server_id:
            return None
        return next((server for server in self._servers if server.id == self._state.server_id), None)

    def _selected_server(self) -> ServerEntry | None:
        selected_items = self.servers_table.selectedItems()
        if not selected_items:
            return None
        row = selected_items[0].row()
        id_item = self.servers_table.item(row, SERVER_TABLE_ID_COLUMN)
        if id_item is None:
            return None
        server_id = id_item.text()
        return next((server for server in self._servers if server.id == server_id), None)

    def _filtered_servers(self) -> list[ServerEntry]:
        query = self.servers_filter.text().strip().lower()
        source = self.servers_source_filter.currentData()
        servers = list(self._servers)
        if source == "favorite":
            servers = [server for server in servers if self._is_favorite_server(server)]
        elif source in {"manual", "subscription"}:
            servers = [server for server in servers if server.source == source]
        if not query:
            return servers
        return [server for server in servers if self._server_matches_query(server, query)]

    def _server_matches_query(self, server: ServerEntry, query: str) -> bool:
        haystack = " ".join(
            (
                server.name,
                server.protocol,
                server.host,
                str(server.port),
                server.source,
                self._server_source_label(server),
                self._subscription_title_for_server(server),
            )
        ).lower()
        return query in haystack

    @staticmethod
    def _sort_servers_by_cached_ping(servers: list[ServerEntry]) -> list[ServerEntry]:
        def sort_key(server: ServerEntry) -> tuple[int, int, int, str, str]:
            ping = server.extra.get("tcp_ping") if isinstance(server.extra, dict) else None
            favorite_rank = 0 if bool(server.extra.get("favorite")) else 1
            if isinstance(ping, dict) and ping.get("ok") and isinstance(ping.get("latency_ms"), int):
                return (favorite_rank, 0, int(ping["latency_ms"]), server.name.lower(), server.host.lower())
            if isinstance(ping, dict) and ping.get("error") == TCP_PING_UNSUPPORTED_ERROR:
                return (favorite_rank, 1, 10**9, server.name.lower(), server.host.lower())
            return (favorite_rank, 2, 10**9, server.name.lower(), server.host.lower())

        return sorted(servers, key=sort_key)

    def _update_best_server_label(self) -> None:
        try:
            best = self.service.best_tcp_ping_server(self._servers)
        except Exception:
            best = None
        card_server = best or self._selected_server()
        if card_server is None:
            self._best_server_id = None
            self.best_server_caption.setText("Лучший TCP ping")
            self.best_server_value.setText("-")
            self.best_server_ping_badge.setText("-")
            self._set_widget_state(self.best_server_ping_badge, "unknown")
            return
        self._best_server_id = card_server.id
        self.best_server_caption.setText("Лучший TCP ping" if best is not None else "Выбранный сервер")
        self.best_server_value.setText(self._compact_text(card_server.name, 92))
        ping = self._tcp_ping_label(card_server)
        self.best_server_ping_badge.setText(ping)
        self._set_widget_state(self.best_server_ping_badge, self._server_ping_state(ping))

    def _update_server_action_state(self) -> None:
        self._refresh_servers_selection_marker()
        self._update_best_server_label()
        server = self._selected_server()
        has_server = server is not None and self._active_worker is None
        is_subscription = bool(server and server.source == "subscription")
        can_edit_link = bool(server and server.source == "manual" and not server.is_amneziawg)
        is_favorite = bool(server and self._is_favorite_server(server))
        self.server_connect_button.setEnabled(
            self._active_worker is None and self._best_server_id is not None and not self._state.is_running
        )
        self.server_ping_selected_button.setEnabled(has_server)
        self.server_ping_all_button.setEnabled(self._active_worker is None and bool(self._servers))
        self.server_update_ping_button.setEnabled(self._active_worker is None and bool(self._servers))
        self.server_details_button.setEnabled(has_server)
        self.server_favorite_button.setEnabled(has_server)
        self.server_favorite_button.setText("★ Убрать из избранного" if is_favorite else "☆ В избранное")
        self._set_widget_state(self.server_favorite_button, "active" if is_favorite else "idle")
        self.server_rename_button.setEnabled(has_server)
        self.server_edit_link_button.setEnabled(has_server and can_edit_link)
        self.server_detach_button.setEnabled(has_server and is_subscription)
        self.server_delete_button.setEnabled(has_server)

    def _set_server_actions_enabled(self, enabled: bool) -> None:
        for button in (
            self.server_add_button,
            self.server_import_button,
            self.server_connect_button,
            self.server_ping_selected_button,
            self.server_ping_all_button,
            self.server_update_ping_button,
            self.server_favorite_button,
            self.server_details_button,
            self.server_rename_button,
            self.server_edit_link_button,
            self.server_detach_button,
            self.server_delete_button,
        ):
            button.setEnabled(enabled)
        for row in range(self.servers_table.rowCount()):
            widget = self.servers_table.cellWidget(row, SERVER_TABLE_ACTIONS_COLUMN)
            if widget is not None:
                widget.setEnabled(enabled and self._active_worker is None)
        self._update_server_action_state()

    def _on_servers_table_item_clicked(self, item: QTableWidgetItem) -> None:
        if item.column() != 1 or self._active_worker is not None:
            return
        id_item = self.servers_table.item(item.row(), SERVER_TABLE_ID_COLUMN)
        if id_item is None:
            return
        self._toggle_server_favorite(id_item.text())

    def _on_servers_table_item_double_clicked(self, item: QTableWidgetItem) -> None:
        if item.column() == 1:
            return
        self._show_selected_server_details()

    def _toggle_selected_server_favorite(self) -> None:
        server = self._selected_server()
        if server is None or self._active_worker is not None:
            return
        self._toggle_server_favorite(server.id)

    def _toggle_server_favorite(self, server_id: str) -> None:
        try:
            updated = self.service.toggle_server_favorite(server_id)
        except Exception as exc:  # noqa: BLE001
            show_error_dialog(self, "Избранное", str(exc))
            return
        for index, server in enumerate(self._servers):
            if server.id == updated.id:
                self._servers[index] = updated
                break
        self._update_server_selector()
        self._update_servers_table()
        self._select_server_row(updated.id)

    def _select_server_row(self, server_id: str) -> None:
        for row in range(self.servers_table.rowCount()):
            id_item = self.servers_table.item(row, SERVER_TABLE_ID_COLUMN)
            if id_item is not None and id_item.text() == server_id:
                self.servers_table.selectRow(row)
                break

    def _ping_selected_server(self) -> None:
        server = self._selected_server()
        if server is None:
            return
        self._run_background_operation(
            "TCP ping",
            f"Проверяем {server.name}...",
            lambda: self.service.run_tcp_ping_for_server(server.id),
            self._on_tcp_ping_finished,
        )

    def _ping_all_servers(self) -> None:
        if not self._servers:
            return
        self._run_background_operation(
            "TCP ping",
            f"Проверяем {len(self._servers)} серверов...",
            lambda: self.service.run_tcp_ping(self._servers),
            self._on_tcp_ping_finished,
        )

    def _ping_connection_servers(self) -> None:
        servers = self._connection_server_candidates() or list(self._servers)
        if not servers:
            return
        self._run_background_operation(
            "TCP ping",
            f"Проверяем {len(servers)} серверов...",
            lambda: self.service.run_tcp_ping(servers),
            self._on_tcp_ping_finished,
        )

    def _on_tcp_ping_finished(self, result: object) -> None:
        results = tuple(getattr(result, "results", ()) or ())
        ok_count = sum(1 for item in results if getattr(item, "ok", False))
        unsupported_count = sum(1 for item in results if getattr(item, "error", None) == TCP_PING_UNSUPPORTED_ERROR)
        failed_count = max(0, len(results) - ok_count - unsupported_count)
        message = f"Проверено: {len(results)}\nДоступно: {ok_count}\nUDP-only: {unsupported_count}\nОшибки: {failed_count}"
        show_info_dialog(self, "TCP ping", message)
        self.refresh_data()

    def _selected_subscription(self) -> SubscriptionEntry | None:
        selected_items = self.subscriptions_table.selectedItems()
        if not selected_items:
            return None
        row = selected_items[0].row()
        id_item = self.subscriptions_table.item(row, 6)
        if id_item is None:
            return None
        subscription_id = id_item.text()
        return next((item for item in self._subscriptions if item.id == subscription_id), None)

    def _update_subscription_action_state(self) -> None:
        subscription = self._selected_subscription()
        has_subscription = subscription is not None and self._active_worker is None
        self.subscription_refresh_button.setEnabled(has_subscription)
        self.subscription_servers_button.setEnabled(has_subscription)
        self.subscription_rename_button.setEnabled(has_subscription)
        self.subscription_edit_url_button.setEnabled(has_subscription)
        self.subscription_delete_button.setEnabled(has_subscription)
        self.subscription_refresh_all_button.setEnabled(self._active_worker is None and bool(self._subscriptions))

    def _set_subscription_actions_enabled(self, enabled: bool) -> None:
        for button in (
            self.subscription_add_button,
            self.subscription_refresh_button,
            self.subscription_refresh_all_button,
            self.subscription_servers_button,
            self.subscription_rename_button,
            self.subscription_edit_url_button,
            self.subscription_delete_button,
        ):
            button.setEnabled(enabled)
        self._update_subscription_action_state()

    def _selected_component_key(self) -> str | None:
        selected_items = self.components_table.selectedItems()
        if not selected_items:
            return None
        row = selected_items[0].row()
        key_item = self.components_table.item(row, 3)
        return key_item.text() if key_item is not None else None

    def _update_component_action_state(self) -> None:
        has_component = self._selected_component_key() is not None and self._active_worker is None
        self.component_update_button.setEnabled(has_component)
        self.component_update_all_button.setEnabled(self._active_worker is None)
        self.app_update_check_button.setEnabled(self._active_worker is None)
        self.app_update_open_button.setEnabled(self._active_worker is None)
        self.app_self_update_button.setEnabled(
            self._active_worker is None
            and self.service.available_app_update() is not None
            and self.service.can_self_update()
        )

    def _set_component_actions_enabled(self, enabled: bool) -> None:
        for button in (
            self.component_update_button,
            self.component_update_all_button,
            self.app_update_check_button,
            self.app_update_open_button,
            self.app_self_update_button,
        ):
            button.setEnabled(enabled)
        self._update_component_action_state()

    def _run_status_healthcheck(self) -> None:
        self._run_background_operation(
            "Health-check",
            "Проверяем доступность сети...",
            lambda: self.service.get_runtime_status(run_healthcheck=True),
            self._on_status_healthcheck_finished,
        )

    def _on_status_healthcheck_finished(self, result: object) -> None:
        health = getattr(result, "healthcheck_result", None)
        show_info_dialog(self, "Health-check", self._healthcheck_label(health))
        self.refresh_data()

    def _update_selected_component(self) -> None:
        key = self._selected_component_key()
        if key is None:
            return
        self._run_background_operation(
            "Обновление компонента",
            "Обновляем компонент...",
            lambda: self.service.update_component(key, stop_active_connection=True),
            self._on_component_update_finished,
        )

    def _update_all_components(self) -> None:
        if not ask_confirmation(
            self,
            "Обновить все компоненты",
            "Обновить все runtime-компоненты? Активное подключение при необходимости будет остановлено.",
            default_yes=True,
        ):
            return
        self._run_background_operation(
            "Обновление компонентов",
            "Обновляем компоненты...",
            lambda: self.service.update_all_components(stop_active_connection=True),
            self._on_component_update_finished,
        )

    def _on_component_update_finished(self, result: object) -> None:
        details = ", ".join(getattr(result, "details", ()) or ()) or "-"
        warnings = tuple(getattr(result, "warnings", ()) or ())
        message = f"Обновлено: {details}"
        if warnings:
            message = f"{message}\n\nПредупреждения:\n" + "\n".join(warnings)
        show_info_dialog(self, "Компоненты", message)
        self.refresh_data()

    def _check_app_update(self) -> None:
        self._run_background_operation(
            "Проверка обновления",
            "Запрашиваем latest release...",
            lambda: self.service.check_app_update(force=True),
            self._on_app_update_checked,
        )

    def _on_app_update_checked(self, result: object) -> None:
        latest = getattr(result, "latest_version", None) or "-"
        is_available = bool(getattr(result, "is_update_available", False))
        error = getattr(result, "error", None)
        if error:
            show_error_dialog(self, "Проверка обновления", str(error))
        elif is_available:
            show_info_dialog(self, "Обновление доступно", f"Доступна версия {latest}.")
        else:
            show_info_dialog(self, "Обновление приложения", f"Новая версия не найдена. Latest: {latest}.")
        self.refresh_data()

    def _open_app_release_page(self) -> None:
        webbrowser.open(self.service.app_release_page_url())

    def _prepare_self_update(self) -> None:
        if not self.service.can_self_update():
            show_error_dialog(self, "Self-update", "Self-update доступен только в packaged Windows .exe сборке.")
            return
        self._run_background_operation(
            "Self-update",
            "Готовим обновление приложения...",
            lambda **kwargs: self.service.prepare_self_update(**kwargs),
            self._on_self_update_prepared,
            progress_kwarg="progress_callback",
        )

    def _on_self_update_prepared(self, result: object) -> None:
        plan = getattr(result, "plan", None)
        release = getattr(result, "release", None)
        version = getattr(release, "latest_version", None) or "-"
        if plan is None:
            show_error_dialog(self, "Self-update", "План обновления не был подготовлен.")
            return
        if not ask_confirmation(
            self,
            "Запустить self-update",
            f"Обновление {version} подготовлено. Запустить helper script и закрыть приложение?",
            default_yes=True,
        ):
            return
        self._run_background_operation(
            "Self-update",
            "Останавливаем VPN и запускаем helper script...",
            lambda: self.service.launch_self_update(plan),
            lambda _result: QTimer.singleShot(0, self._finish_exit),
        )

    def _selected_routing_profile(self) -> RoutingProfile | None:
        profile_id = self.settings_routing_combo.currentData()
        if profile_id is None:
            return None
        return next((profile for profile in self._routing_profiles if profile.profile_id == profile_id), None)

    def _update_selected_routing_profile_description(self, *_args: object) -> None:
        profile = self._selected_routing_profile()
        if profile is None:
            self.settings_routing_description.setText("-")
            self.settings_profile_details_button.setEnabled(False)
            return
        self.settings_routing_description.setText(profile.description or "-")
        self.settings_profile_details_button.setEnabled(True)

    def _show_selected_routing_profile_details(self) -> None:
        profile = self._selected_routing_profile()
        if profile is None:
            return
        rules_count = len(profile.rules)
        details = (
            f"Название: {profile.name}\n"
            f"ID: {profile.profile_id}\n"
            f"Описание: {profile.description or '-'}\n"
            f"Правил: {rules_count}"
        )
        show_info_dialog(self, "Профиль маршрутизации", details)

    def _save_settings_from_form(self) -> None:
        profile_id = self.settings_routing_combo.currentData()
        try:
            self._settings = self.service.update_settings(
                connection_mode=str(self.settings_mode_combo.currentData() or "PROXY"),
                set_system_proxy=self.settings_system_proxy_checkbox.isChecked(),
                auto_update_subscriptions_on_startup=self.settings_auto_update_checkbox.isChecked(),
                active_routing_profile_id=str(profile_id) if profile_id is not None else None,
            )
        except Exception as exc:  # noqa: BLE001
            show_error_dialog(self, "Ошибка настроек", str(exc))
            return
        show_info_dialog(self, "Настройки сохранены", "Параметры приложения обновлены.")
        self.refresh_data()

    def _add_subscription(self) -> None:
        url, ok = ask_text(
            self,
            "Добавить подписку",
            "URL подписки:",
        )
        if not ok or not url.strip():
            return
        self._run_background_operation(
            "Добавление подписки",
            "Загружаем подписку...",
            lambda: self.service.add_subscription_url(url),
            self._on_subscription_import_finished,
        )

    def _on_subscription_import_finished(self, result: object) -> None:
        count = len(getattr(result, "servers", ()) or ())
        subscription = getattr(result, "subscription", None)
        title = getattr(subscription, "title", "Подписка")
        show_info_dialog(self, "Подписка сохранена", f"{title}\nСерверов: {count}")
        self.refresh_data()

    def _refresh_selected_subscription(self) -> None:
        subscription = self._selected_subscription()
        if subscription is None:
            return
        self._run_background_operation(
            "Обновление подписки",
            "Загружаем подписку...",
            lambda: self.service.refresh_subscription(subscription.id),
            lambda result: self._on_subscription_refresh_finished(subscription.title, result),
        )

    def _on_subscription_refresh_finished(self, title: str, result: object) -> None:
        count = len(result) if isinstance(result, list) else 0
        show_info_dialog(self, "Подписка обновлена", f"{title}\nСерверов: {count}")
        self.refresh_data()

    def _refresh_all_subscriptions(self) -> None:
        if not self._subscriptions:
            return
        self._run_background_operation(
            "Обновление подписок",
            "Обновляем все подписки...",
            lambda: self.service.refresh_subscriptions(),
            self._on_refresh_all_subscriptions_finished,
        )

    def _on_refresh_all_subscriptions_finished(self, result: object) -> None:
        success = getattr(result, "success", ()) or ()
        failed = getattr(result, "failed", ()) or ()
        message = f"Успешно: {len(success)}\nС ошибками: {len(failed)}"
        if failed:
            details = "\n".join(f"{subscription.title}: {error}" for subscription, error in failed[:5])
            message = f"{message}\n\n{details}"
        show_info_dialog(self, "Обновление подписок", message)
        self.refresh_data()

    def _show_selected_subscription_servers(self) -> None:
        subscription = self._selected_subscription()
        if subscription is None:
            return
        try:
            servers = self.service.subscription_servers(subscription.id)
        except Exception as exc:  # noqa: BLE001
            show_error_dialog(self, "Ошибка подписки", str(exc))
            return
        dialog = QDialog(self)
        dialog.setWindowTitle(f"Серверы подписки: {subscription.title}")
        dialog.resize(760, 420)
        layout = QVBoxLayout(dialog)
        table = QTableWidget(0, 4)
        self._configure_table(table, ("Название", "Протокол", "Host", "Port"))
        table.setRowCount(len(servers))
        for row, server in enumerate(servers):
            self._set_row(
                table,
                row,
                (server.name, server.protocol.upper(), server.host, str(server.port)),
            )
        table.resizeColumnsToContents()
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close_button = buttons.button(QDialogButtonBox.StandardButton.Close)
        if close_button is not None:
            close_button.setText("Закрыть")
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(self._table_surface(table), 1)
        layout.addWidget(buttons)
        dialog.exec()

    def _rename_selected_subscription(self) -> None:
        subscription = self._selected_subscription()
        if subscription is None:
            return
        title, ok = ask_text(
            self,
            "Переименовать подписку",
            "Новое название:",
            default=subscription.title,
        )
        if not ok or not title.strip() or title.strip() == subscription.title:
            return
        try:
            self.service.rename_subscription(subscription.id, title)
        except Exception as exc:  # noqa: BLE001
            show_error_dialog(self, "Ошибка подписки", str(exc))
            return
        self.refresh_data()

    def _edit_selected_subscription_url(self) -> None:
        subscription = self._selected_subscription()
        if subscription is None:
            return
        url, ok = ask_text(
            self,
            "Изменить URL подписки",
            "Новый URL:",
            default=subscription.url,
        )
        if not ok or not url.strip() or url.strip() == subscription.url:
            return
        self._run_background_operation(
            "Обновление URL подписки",
            "Проверяем новый URL и загружаем серверы...",
            lambda: self.service.update_subscription_url(subscription.id, url),
            self._on_subscription_url_updated,
        )

    def _on_subscription_url_updated(self, result: object) -> None:
        imported = result[1] if isinstance(result, tuple) and len(result) == 2 else []
        show_info_dialog(self, "URL подписки обновлен", f"Серверов: {len(imported)}")
        self.refresh_data()

    def _delete_selected_subscription(self) -> None:
        subscription = self._selected_subscription()
        if subscription is None:
            return
        remove_servers = ask_confirmation(
            self,
            "Удалить подписку",
            f"Удалить подписку '{subscription.title}' вместе с ее серверами?\n\n"
            "Можно удалить только запись подписки и оставить ее серверы как ручные.",
            default_yes=False,
            yes_text="Удалить вместе",
            no_text="Только подписку",
        )
        if not ask_confirmation(
            self,
            "Подтверждение удаления",
            f"Подтвердите удаление подписки '{subscription.title}'.",
            default_yes=False,
        ):
            return
        disconnect_active = False
        subscription_server_ids = {
            server.id
            for server in self._servers
            if server.source == "subscription" and server.subscription_id == subscription.id
        }
        if remove_servers and self._state.is_running and self._state.server_id in subscription_server_ids:
            disconnect_active = ask_confirmation(
                self,
                "Активное подключение",
                "Сервер этой подписки сейчас активен. Отключить текущее подключение и продолжить?",
                default_yes=True,
            )
            if not disconnect_active:
                return
        self._run_background_operation(
            "Удаление подписки",
            "Удаляем подписку...",
            lambda: self.service.delete_subscription(
                subscription.id,
                remove_servers=remove_servers,
                disconnect_active=disconnect_active,
            ),
            lambda _result: self.refresh_data(),
        )

    def _add_server_from_share_link(self) -> None:
        raw_link, ok = ask_text(
            self,
            "Добавить сервер",
            "Вставьте share link сервера:",
        )
        if not ok or not raw_link.strip():
            return
        self._run_import_operation(raw_link)

    def _quick_import_servers(self) -> None:
        raw_value = ask_multiline_text(
            self,
            "Быстрый импорт",
            "Вставьте одну или несколько ссылок, URL подписки, vpn:// payload, AWG-конфиг или JSON:",
        )
        if raw_value is None or not raw_value.strip():
            return
        self._run_import_operation(raw_value)

    def _run_import_operation(self, raw_value: str) -> None:
        self._run_background_operation(
            "Импорт серверов",
            "Импортируем данные...",
            lambda: self.service.import_links(raw_value),
            self._on_import_finished,
        )

    def _on_import_finished(self, result: object) -> None:
        count = len(getattr(result, "servers", ()) or ())
        kind = getattr(result, "kind", "import")
        if kind.startswith("subscription"):
            message = f"Подписка сохранена. Импортировано серверов: {count}."
        else:
            message = f"Импортировано серверов: {count}."
        show_info_dialog(self, "Импорт выполнен", message)
        self.refresh_data()

    def _connect_best_server(self) -> None:
        if self._active_worker is not None or self._state.is_running:
            return
        server_id = self._best_server_id
        if server_id is None:
            selected = self._selected_server()
            server_id = selected.id if selected is not None else None
        if server_id is None:
            return
        self._connect_server_id(server_id)

    def _connect_selected_server(self) -> None:
        server = self._selected_server()
        if server is None:
            return
        self._connect_server_id(server.id)

    def _show_selected_server_details(self) -> None:
        server = self._selected_server()
        if server is None:
            return
        rows = [
            ("Название", server.name),
            ("Протокол", server.protocol.upper()),
            ("Host", server.host),
            ("Port", str(server.port)),
            ("Источник", self._server_source_label(server)),
            ("Избранное", "Да" if self._is_favorite_server(server) else "Нет"),
            ("Подписка", self._subscription_title_for_server(server) or "-"),
            ("TCP ping", self._tcp_ping_label(server)),
            ("Статус", "Активен" if self._state.server_id == server.id and self._state.is_running else "-"),
            ("Raw link", server.raw_link or "-"),
        ]
        details = "\n".join(f"{name}: {value}" for name, value in rows)
        show_info_dialog(self, "Детали сервера", details)

    def _rename_selected_server(self) -> None:
        server = self._selected_server()
        if server is None:
            return
        new_name, ok = ask_text(
            self,
            "Переименовать сервер",
            "Новое имя сервера:",
            default=server.name,
        )
        if not ok or not new_name.strip() or new_name.strip() == server.name:
            return
        try:
            self.service.rename_server(server.id, new_name)
        except Exception as exc:  # noqa: BLE001
            show_error_dialog(self, "Ошибка сервера", str(exc))
            return
        self.refresh_data()

    def _edit_selected_server_link(self) -> None:
        server = self._selected_server()
        if server is None:
            return
        raw_link = ask_multiline_text(
            self,
            "Изменить ссылку сервера",
            "Новая share link:",
            default=server.raw_link,
        )
        if raw_link is None or not raw_link.strip() or raw_link.strip() == server.raw_link:
            return
        disconnect_active = False
        if self._state.is_running and self._state.server_id == server.id:
            disconnect_active = ask_confirmation(
                self,
                "Активный сервер",
                "Этот сервер сейчас активен. Отключить текущее подключение и сохранить новую ссылку?",
                default_yes=True,
            )
            if not disconnect_active:
                return
        self._run_background_operation(
            "Обновление сервера",
            "Сохраняем новую ссылку...",
            lambda: self.service.update_server_link(
                server.id,
                raw_link,
                disconnect_active=disconnect_active,
            ),
            lambda _result: self.refresh_data(),
        )

    def _detach_selected_server(self) -> None:
        server = self._selected_server()
        if server is None:
            return
        if not ask_confirmation(
            self,
            "Отвязать сервер",
            f"Отвязать сервер '{server.name}' от подписки и оставить как ручной?",
            default_yes=True,
        ):
            return
        try:
            self.service.detach_server_from_subscription(server.id)
        except Exception as exc:  # noqa: BLE001
            show_error_dialog(self, "Ошибка сервера", str(exc))
            return
        self.refresh_data()

    def _delete_selected_server(self) -> None:
        server = self._selected_server()
        if server is None:
            return
        if server.source == "subscription":
            detach = ask_confirmation(
                self,
                "Сервер из подписки",
                "Сервер импортирован из подписки. Отвязать от подписки и оставить как ручной?\n\n"
                "Нажмите 'Нет', чтобы перейти к удалению из списка.",
                default_yes=False,
            )
            if detach:
                self._detach_selected_server()
                return
        message = f"Удалить сервер '{server.name}'?"
        if server.source == "subscription":
            message += "\nПосле следующего обновления подписки он может появиться снова."
        if not ask_confirmation(self, "Удалить сервер", message, default_yes=False):
            return
        disconnect_active = False
        if self._state.is_running and self._state.server_id == server.id:
            disconnect_active = ask_confirmation(
                self,
                "Активный сервер",
                "Этот сервер сейчас активен. Отключить текущее подключение и удалить сервер?",
                default_yes=True,
            )
            if not disconnect_active:
                return
        self._run_background_operation(
            "Удаление сервера",
            "Удаляем сервер...",
            lambda: self.service.delete_server(server.id, disconnect_active=disconnect_active),
            lambda _result: self.refresh_data(),
        )

    def _active_routing_profile_label(self) -> str:
        try:
            settings = self._settings
            for profile in self.service.list_routing_profiles():
                if profile.profile_id == settings.active_routing_profile_id:
                    return profile.name
        except Exception:
            return "-"
        return "-"

    @staticmethod
    def _backend_label(backend_id: str | None) -> str:
        if backend_id == "singbox":
            return "sing-box"
        if backend_id == "amneziawg":
            return "AmneziaWG"
        return "Xray"

    @staticmethod
    def _server_source_label(server: ServerEntry) -> str:
        if server.source == "subscription":
            return "Подписка"
        return "Ручной"

    def _subscription_title_for_server(self, server: ServerEntry) -> str:
        if not server.subscription_id:
            return ""
        subscription = next(
            (item for item in self._subscriptions if item.id == server.subscription_id),
            None,
        )
        return subscription.title if subscription is not None else server.subscription_id

    @staticmethod
    def _short_datetime(value: str | None) -> str:
        normalized = str(value or "").strip()
        if not normalized:
            return "-"
        normalized = normalized.replace("T", " ")
        for separator in ("+", "Z"):
            if separator in normalized:
                normalized = normalized.split(separator, 1)[0]
        return normalized[:19] or "-"

    @staticmethod
    def _tcp_ping_label(server: ServerEntry) -> str:
        ping = server.extra.get("tcp_ping") if isinstance(server.extra, dict) else None
        if not isinstance(ping, dict):
            return "-"
        if ping.get("ok") and ping.get("latency_ms") is not None:
            return f"{ping['latency_ms']} ms"
        if ping.get("error") == TCP_PING_UNSUPPORTED_ERROR:
            return "UDP-only"
        return str(ping.get("error") or "-")

    @staticmethod
    def _healthcheck_label(health_result: object | None) -> str:
        if health_result is None:
            return "-"
        ok = bool(getattr(health_result, "ok", False))
        message = str(getattr(health_result, "message", "") or "-")
        checked_url = getattr(health_result, "checked_url", None)
        if ok:
            return f"OK: {checked_url or message}"
        if bool(getattr(health_result, "inconclusive", False)):
            return f"Не подтвержден: {message}"
        return f"Ошибка: {message}"
