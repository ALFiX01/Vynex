from __future__ import annotations

FONT_FAMILY = "Segoe UI"
FONT_POINT_SIZE = 10

SPACE_0 = 0
SPACE_1 = 4
SPACE_2 = 8
SPACE_3 = 12
SPACE_4 = 16
SPACE_5 = 20
SPACE_6 = 24
SPACE_7 = 28
SPACE_8 = 32
SPACE_12 = 48
HAIRLINE = 1

TEXT_XS = "11px"
TEXT_SM = "13px"
TEXT_BASE = "15px"
TEXT_LG = "17px"
TEXT_XL = "20px"
TEXT_2XL = "24px"

LINE_XS = "16px"
LINE_SM = "18px"
LINE_BASE = "20px"
LINE_LG = "24px"
LINE_XL = "28px"
LINE_2XL = "32px"

RADIUS_SM = "4px"
RADIUS_MD = "8px"
RADIUS_LG = "12px"

DURATION_FAST_MS = 120
DURATION_BASE_MS = 200
EASING_STANDARD = "cubic-bezier(0.4, 0, 0.2, 1)"

COLOR_BG = "#0B111A"
COLOR_BG_SIDEBAR = COLOR_BG
COLOR_SURFACE = "#111827"
COLOR_SURFACE_ALT = "#0F1724"
COLOR_SURFACE_MUTED = COLOR_SURFACE_ALT
COLOR_SURFACE_HOVER = "#17233A"
COLOR_SURFACE_ACTIVE = COLOR_SURFACE_ALT
COLOR_BORDER = "#26364D"
COLOR_BORDER_MUTED = COLOR_BORDER
COLOR_BORDER_STRONG = "#5E7DFF"

COLOR_TEXT_PRIMARY = "#F8FAFC"
COLOR_TEXT_SECONDARY = "#CBD5E1"
COLOR_TEXT_MUTED = "#94A3B8"
COLOR_TEXT_DISABLED = COLOR_TEXT_MUTED
COLOR_TEXT_INVERSE = COLOR_TEXT_PRIMARY

COLOR_PRIMARY = "#3862F6"
COLOR_PRIMARY_HOVER = "#4F75FF"
COLOR_PRIMARY_ACTIVE = "#1D4ED8"
COLOR_PRIMARY_MUTED = "#17233A"
COLOR_PRIMARY_SOFT = "#7EA0FF"
COLOR_PRIMARY_BORDER = "#5E7DFF"
COLOR_FOCUS = "#7EA0FF"
COLOR_SELECTION = COLOR_PRIMARY_ACTIVE

COLOR_SUCCESS = "#22C55E"
COLOR_SUCCESS_BG = "#123726"
COLOR_SUCCESS_BORDER = "#1F6A42"
COLOR_WARNING = "#FACC15"
COLOR_WARNING_BG = "#3A3014"
COLOR_WARNING_BORDER = "#705D20"
COLOR_DANGER = "#EF4444"
COLOR_DANGER_BG = "#3B1820"
COLOR_DANGER_BORDER = "#74303D"
COLOR_INFO = COLOR_PRIMARY_SOFT
COLOR_INFO_BG = "#172334"
COLOR_INFO_BORDER = "#2B415F"

CONTROL_HEIGHT = 36
CONTROL_HEIGHT_COMPACT = 32
CHIP_HEIGHT = 28
PAGER_SIZE = 28
TABLE_ROW_HEIGHT = 36
SERVERS_TABLE_ROW_HEIGHT = 44
SERVER_TABLE_CHECK_COLUMN_WIDTH = 48
SERVER_TABLE_PROTOCOL_COLUMN_WIDTH = 120
SERVER_TABLE_PROTOCOL_BADGE_MIN_WIDTH = 88
SERVER_TABLE_PROTOCOL_BADGE_HEIGHT = 24
ICON_SIZE_SM = 16
HERO_METRIC_ICON_SIZE = 18
ICON_SIZE_MD = 20
ICON_SIZE_LG = 24
ICON_SIZE_XL = 36
BUTTON_MIN_WIDTH = 96
SERVER_SELECTION_CARD_HEIGHT = 80
CONNECTION_SERVER_LIST_VISIBLE_ROWS = 2
CONNECTION_SERVER_LIST_HEIGHT = (
    SERVER_SELECTION_CARD_HEIGHT * CONNECTION_SERVER_LIST_VISIBLE_ROWS
    + SPACE_2 * (CONNECTION_SERVER_LIST_VISIBLE_ROWS - 1)
)

SHADOW_SM = "none"
SHADOW_MD = "none"


def px(value: int | float) -> str:
    return f"{value:g}px"


def spacing(*values: int) -> tuple[int, ...]:
    return values


def app_stylesheet() -> str:
    values = {
        "font_family": FONT_FAMILY,
        "text_xs": TEXT_XS,
        "text_sm": TEXT_SM,
        "text_base": TEXT_BASE,
        "text_lg": TEXT_LG,
        "text_xl": TEXT_XL,
        "text_2xl": TEXT_2XL,
        "line_xs": LINE_XS,
        "line_sm": LINE_SM,
        "line_base": LINE_BASE,
        "line_lg": LINE_LG,
        "line_xl": LINE_XL,
        "line_2xl": LINE_2XL,
        "radius_sm": RADIUS_SM,
        "radius_md": RADIUS_MD,
        "radius_lg": RADIUS_LG,
        "bg": COLOR_BG,
        "bg_sidebar": COLOR_BG_SIDEBAR,
        "surface": COLOR_SURFACE,
        "surface_alt": COLOR_SURFACE_ALT,
        "surface_muted": COLOR_SURFACE_MUTED,
        "surface_hover": COLOR_SURFACE_HOVER,
        "surface_active": COLOR_SURFACE_ACTIVE,
        "border": COLOR_BORDER,
        "border_muted": COLOR_BORDER_MUTED,
        "border_strong": COLOR_BORDER_STRONG,
        "text_primary": COLOR_TEXT_PRIMARY,
        "text_secondary": COLOR_TEXT_SECONDARY,
        "text_muted": COLOR_TEXT_MUTED,
        "text_disabled": COLOR_TEXT_DISABLED,
        "text_inverse": COLOR_TEXT_INVERSE,
        "primary": COLOR_PRIMARY,
        "primary_hover": COLOR_PRIMARY_HOVER,
        "primary_active": COLOR_PRIMARY_ACTIVE,
        "primary_muted": COLOR_PRIMARY_MUTED,
        "primary_soft": COLOR_PRIMARY_SOFT,
        "primary_border": COLOR_PRIMARY_BORDER,
        "focus": COLOR_FOCUS,
        "selection": COLOR_SELECTION,
        "success": COLOR_SUCCESS,
        "success_bg": COLOR_SUCCESS_BG,
        "success_border": COLOR_SUCCESS_BORDER,
        "warning": COLOR_WARNING,
        "warning_bg": COLOR_WARNING_BG,
        "warning_border": COLOR_WARNING_BORDER,
        "danger": COLOR_DANGER,
        "danger_bg": COLOR_DANGER_BG,
        "danger_border": COLOR_DANGER_BORDER,
        "info": COLOR_INFO,
        "info_bg": COLOR_INFO_BG,
        "info_border": COLOR_INFO_BORDER,
        "space_0": px(SPACE_0),
        "space_1": px(SPACE_1),
        "space_2": px(SPACE_2),
        "space_3": px(SPACE_3),
        "space_4": px(SPACE_4),
        "space_5": px(SPACE_5),
        "space_6": px(SPACE_6),
        "space_7": px(SPACE_7),
        "space_8": px(SPACE_8),
        "space_12": px(SPACE_12),
        "control_height": px(CONTROL_HEIGHT),
        "control_compact_height": px(CONTROL_HEIGHT_COMPACT),
        "chip_height": px(CHIP_HEIGHT),
        "pager_size": px(PAGER_SIZE),
        "table_row_height": px(TABLE_ROW_HEIGHT),
        "icon_size_sm": px(ICON_SIZE_SM),
        "hero_metric_icon_size": px(HERO_METRIC_ICON_SIZE),
        "icon_size_md": px(ICON_SIZE_MD),
        "button_min_width": px(BUTTON_MIN_WIDTH),
    }
    return _APP_STYLESHEET % values


_APP_STYLESHEET = """
* {
    font-family: "%(font_family)s";
    font-size: %(text_sm)s;
}
QMainWindow, QWidget, QDialog {
    background: %(bg)s;
    color: %(text_primary)s;
    selection-background-color: %(selection)s;
    selection-color: %(text_inverse)s;
}
QLabel, QCheckBox {
    background: transparent;
    border: 0;
}
QWidget#Content,
QWidget#ServersPage {
    background: %(bg)s;
    color: %(text_primary)s;
}
QWidget#Transparent,
QFrame#Toolbar,
QFrame#ServersSearchBar,
QFrame#ServersImportBar,
QFrame#ServersActionBar,
QFrame#ServersSecondaryBar,
QFrame#ServersActionsBar,
QFrame#ServersFooter {
    background: transparent;
    border: 0;
    padding: %(space_0)s;
}
QFrame#Sidebar {
    background: %(bg_sidebar)s;
    border-right: 1px solid %(border)s;
}
QFrame#Panel,
QFrame#TableSurface,
QFrame#ServersSearchCard,
QFrame#ServersBestCard,
QFrame#ServersBestBar,
QFrame#EmptyState,
QFrame#ServerSelectionCard {
    background: %(surface)s;
    border: 1px solid %(border)s;
    border-radius: %(radius_md)s;
}
QFrame#TableSurface {
    background: %(surface_alt)s;
}
QFrame#ConnectionHero {
    background: %(surface)s;
    border: 1px solid %(border_strong)s;
    border-radius: %(radius_md)s;
}
QScrollArea#ConnectionServerScroll,
QWidget#ConnectionServerViewport,
QWidget#ConnectionServerList {
    background: %(surface)s;
    border: 0;
}
QFrame#ServerSelectionCard:hover {
    background: %(surface_hover)s;
    border-color: %(primary)s;
}
QFrame#ServerSelectionCard[state="selected"] {
    background: %(primary_muted)s;
    border-color: %(primary_border)s;
}
QFrame#ServerSelectionCard:disabled {
    background: %(surface_muted)s;
    border-color: %(border_muted)s;
}
QLabel#AppTitle {
    background: transparent;
    color: %(text_primary)s;
    font-size: %(text_xl)s;
    font-weight: 700;
    line-height: %(line_xl)s;
}
QLabel#ConnectionServerCount {
    background: transparent;
    color: %(text_muted)s;
    font-size: %(text_xs)s;
    line-height: %(line_xs)s;
}
QListWidget#Navigation {
    background: transparent;
    border: 0;
    outline: 0;
    color: %(text_secondary)s;
}
QListWidget#Navigation::item {
    border-radius: %(radius_md)s;
    min-height: %(control_compact_height)s;
    padding: %(space_2)s %(space_3)s;
}
QListWidget#Navigation::item:selected {
    background: %(primary)s;
    color: %(text_inverse)s;
}
QListWidget#Navigation::item:hover {
    background: %(primary_muted)s;
    color: %(text_primary)s;
}
QLabel#HeaderTitle {
    background: transparent;
    color: %(text_primary)s;
    font-size: %(text_lg)s;
    font-weight: 700;
    line-height: %(line_lg)s;
}
QLabel#PageTitle {
    background: transparent;
    color: %(text_primary)s;
    font-size: %(text_2xl)s;
    font-weight: 700;
    line-height: %(line_2xl)s;
}
QWidget#ServersPage QLabel#PageTitle {
    font-size: %(text_xl)s;
    font-weight: 700;
    line-height: %(line_xl)s;
}
QLabel#PageSubtitle,
QLabel#ConnectionStatusDetail,
QLabel#ConnectionServerMeta,
QLabel#EmptyText {
    background: transparent;
    color: %(text_muted)s;
    font-size: %(text_sm)s;
    line-height: %(line_sm)s;
}
QLabel#BestServerCaption,
QLabel#FieldCaption,
QLabel#PanelTitle,
QLabel#HeroMetricCaption {
    background: transparent;
    color: %(text_muted)s;
    font-size: %(text_sm)s;
    font-weight: 600;
    line-height: %(line_sm)s;
}
QLabel#BestServerName,
QLabel#FieldValue,
QLabel#EmptyTitle,
QLabel#ConnectionServerTitle,
QLabel#HeroMetricValue {
    background: transparent;
    color: %(text_primary)s;
    font-size: %(text_base)s;
    font-weight: 700;
    line-height: %(line_base)s;
}
QLabel#ConnectionStatusBadge {
    background: transparent;
    color: %(text_primary)s;
    font-size: %(text_2xl)s;
    font-weight: 800;
    line-height: %(line_2xl)s;
    min-height: %(space_8)s;
    padding: %(space_0)s;
}
QLabel#ConnectionStatusBadge[state="connected"] {
    color: %(success)s;
}
QLabel#ConnectionStatusBadge[state="busy"] {
    color: %(info)s;
}
QLabel#ConnectionStatusBadge[state="error"] {
    color: %(danger)s;
}
QFrame#HeroSeparator,
QFrame#HeroMetricDivider {
    background: %(border)s;
    border: 0;
}
QWidget#HeroMetric {
    background: transparent;
    border: 0;
    min-height: %(space_12)s;
}
QLabel#HeroMetricIcon {
    background: transparent;
    border: 0;
    color: %(primary_soft)s;
    font-size: %(text_2xl)s;
    font-weight: 800;
    min-height: %(hero_metric_icon_size)s;
    min-width: %(hero_metric_icon_size)s;
    max-height: %(hero_metric_icon_size)s;
    max-width: %(hero_metric_icon_size)s;
}
QLabel#ServerCardIcon {
    background: %(primary_muted)s;
    border: 1px solid %(border_strong)s;
    border-radius: %(radius_md)s;
    color: %(primary_soft)s;
    font-size: %(text_base)s;
    font-weight: 800;
}
QLabel#ServerCardTitle {
    background: transparent;
    color: %(text_primary)s;
    font-size: %(text_sm)s;
    font-weight: 700;
}
QLabel#ServerCardMeta {
    background: transparent;
    color: %(text_muted)s;
    font-size: %(text_xs)s;
}
QLabel#BestPingBadge,
QLabel#ServerCardPing,
QLabel#ServerCardSelected,
QLabel#ProtocolBadge,
QLabel#SourceBadge,
QLabel#PingBadge,
QLabel#StatusPill {
    background: %(surface_alt)s;
    border: 1px solid %(border)s;
    border-radius: %(radius_sm)s;
    color: %(text_secondary)s;
    font-size: %(text_xs)s;
    font-weight: 800;
    min-height: %(chip_height)s;
    padding: %(space_1)s %(space_2)s;
}
QLabel#ProtocolBadge {
    min-height: %(space_5)s;
    padding: 1px %(space_2)s;
}
QLabel#ProtocolBadge[state="vless"],
QLabel#ProtocolBadge[state="vmess"],
QLabel#ProtocolBadge[state="trojan"],
QLabel#ProtocolBadge[state="amneziawg"],
QLabel#ProtocolBadge[state="other"] {
    background: %(primary_muted)s;
    border-color: %(border_strong)s;
    color: %(primary_soft)s;
}
QLabel#SourceBadge[state="manual"],
QLabel#SourceBadge[state="subscription"],
QLabel#BestPingBadge[state="unknown"],
QLabel#PingBadge[state="unknown"],
QLabel#StatusPill[state="unknown"] {
    background: %(surface_alt)s;
    border-color: %(border)s;
    color: %(text_muted)s;
}
QLabel#BestPingBadge,
QLabel#BestPingBadge[state="ok"],
QLabel#ServerCardPing[state="ok"],
QLabel#PingBadge[state="ok"],
QLabel#StatusPill[state="ok"],
QLabel#StatusPill[state="active"] {
    background: %(success_bg)s;
    border-color: %(success_border)s;
    color: %(success)s;
}
QLabel#BestPingBadge[state="slow"],
QLabel#PingBadge[state="slow"],
QLabel#StatusPill[state="slow"] {
    background: %(warning_bg)s;
    border-color: %(warning_border)s;
    color: %(warning)s;
}
QLabel#BestPingBadge[state="udp"],
QLabel#PingBadge[state="udp"],
QLabel#StatusPill[state="udp"] {
    background: %(info_bg)s;
    border-color: %(info_border)s;
    color: %(info)s;
}
QLabel#BestPingBadge[state="error"],
QLabel#PingBadge[state="error"],
QLabel#StatusPill[state="error"] {
    background: %(danger_bg)s;
    border-color: %(danger_border)s;
    color: %(danger)s;
}
QLabel#ServerCardSelected {
    background: %(primary)s;
    border-color: %(primary_border)s;
    color: %(text_inverse)s;
}
QLabel#ConnectionServerEmpty {
    background: %(surface)s;
    border: 1px solid %(border)s;
    border-radius: %(radius_md)s;
    color: %(text_muted)s;
    font-size: %(text_sm)s;
}
QGroupBox {
    background: transparent;
    border: 0;
    margin-top: %(space_0)s;
    padding: %(space_0)s;
    font-weight: 600;
}
QGroupBox::title {
    color: transparent;
    background: transparent;
    border: 0;
    padding: %(space_0)s;
    height: %(space_0)s;
    margin: %(space_0)s;
}
QFrame#Panel > QLabel {
    background: transparent;
    border: 0;
}
QPushButton {
    background: %(surface)s;
    border: 1px solid %(border)s;
    border-radius: %(radius_md)s;
    color: %(text_primary)s;
    font-weight: 600;
    min-height: %(control_height)s;
    padding: %(space_2)s %(space_3)s;
}
QPushButton:hover {
    background: %(surface_hover)s;
    border-color: %(primary)s;
    color: %(text_inverse)s;
}
QPushButton:pressed {
    background: %(surface_active)s;
    border-color: %(primary_soft)s;
}
QPushButton:focus {
    border-color: %(focus)s;
}
QPushButton:disabled {
    background: %(surface_muted)s;
    border-color: %(border_muted)s;
    color: %(text_disabled)s;
}
QPushButton#PrimaryButton {
    background: %(primary)s;
    border-color: %(primary_border)s;
    color: %(text_inverse)s;
}
QPushButton#PrimaryButton:hover {
    background: %(primary_hover)s;
    border-color: %(primary_soft)s;
}
QPushButton#PrimaryButton:pressed {
    background: %(primary_active)s;
}
QPushButton#PrimaryButton:disabled {
    background: %(surface_muted)s;
    border-color: %(border_muted)s;
    color: %(text_disabled)s;
}
QPushButton#DangerButton {
    background: %(danger_bg)s;
    border-color: %(danger_border)s;
    color: %(danger)s;
}
QPushButton#DangerButton:hover {
    background: %(danger_border)s;
    border-color: %(danger)s;
    color: %(text_inverse)s;
}
QPushButton#DangerButton:pressed {
    background: %(danger_bg)s;
}
QPushButton#DangerButton:disabled {
    background: %(surface_muted)s;
    border-color: %(border_muted)s;
    color: %(text_disabled)s;
}
QPushButton#FavoriteButton {
    background: %(surface)s;
    border-color: %(border)s;
    color: %(text_secondary)s;
}
QPushButton#FavoriteButton[state="active"] {
    background: %(warning_bg)s;
    border-color: %(warning_border)s;
    color: %(warning)s;
}
QPushButton#FavoriteButton:hover,
QPushButton#FavoriteButton[state="active"]:hover {
    background: %(surface_hover)s;
    border-color: %(primary)s;
    color: %(text_inverse)s;
}
QPushButton#FavoriteButton:pressed {
    background: %(surface_active)s;
}
QPushButton#FavoriteButton:disabled {
    background: %(surface_muted)s;
    border-color: %(border_muted)s;
    color: %(text_disabled)s;
}
QPushButton#SubtleButton {
    background: %(surface)s;
    border-color: %(border)s;
    color: %(text_secondary)s;
}
QPushButton#SubtleButton:hover {
    background: %(surface_hover)s;
    border-color: %(primary)s;
}
QPushButton#SubtleButton:disabled {
    background: %(surface_muted)s;
    border-color: %(border_muted)s;
    color: %(text_disabled)s;
}
QPushButton#OutlinedButton {
    background: transparent;
    border-color: %(primary_border)s;
    color: %(primary_soft)s;
}
QPushButton#OutlinedButton:hover {
    background: %(primary_muted)s;
    border-color: %(primary_soft)s;
    color: %(text_inverse)s;
}
QPushButton#OutlinedButton:pressed {
    background: %(surface_active)s;
    border-color: %(primary)s;
}
QPushButton#OutlinedButton:disabled {
    background: transparent;
    border-color: %(border_muted)s;
    color: %(text_disabled)s;
}
QLineEdit, QTextEdit, QComboBox {
    background: %(bg)s;
    border: 1px solid %(border)s;
    border-radius: %(radius_md)s;
    color: %(text_primary)s;
    min-height: %(control_height)s;
    padding: %(space_2)s %(space_3)s;
}
QLineEdit:hover, QTextEdit:hover, QComboBox:hover {
    border-color: %(border_strong)s;
}
QLineEdit:focus, QTextEdit:focus, QComboBox:focus {
    border-color: %(focus)s;
    background: %(surface)s;
}
QLineEdit:disabled, QTextEdit:disabled, QComboBox:disabled {
    background: %(surface_muted)s;
    border-color: %(border_muted)s;
    color: %(text_disabled)s;
}
QComboBox::drop-down {
    border: 0;
    width: %(space_8)s;
}
QComboBox QAbstractItemView {
    background: %(surface)s;
    border: 1px solid %(border)s;
    color: %(text_primary)s;
    selection-background-color: %(selection)s;
    selection-color: %(text_inverse)s;
    outline: 0;
}
QCheckBox {
    color: %(text_secondary)s;
    spacing: %(space_2)s;
}
QCheckBox:hover {
    color: %(text_primary)s;
}
QCheckBox:disabled {
    color: %(text_disabled)s;
}
QCheckBox::indicator {
    background: %(surface_alt)s;
    border: 1px solid %(border)s;
    border-radius: %(radius_sm)s;
    height: %(icon_size_sm)s;
    width: %(icon_size_sm)s;
}
QCheckBox::indicator:hover,
QCheckBox::indicator:focus {
    border-color: %(focus)s;
}
QCheckBox::indicator:checked {
    background: %(primary)s;
    border-color: %(primary_soft)s;
}
QCheckBox::indicator:disabled {
    background: %(surface_muted)s;
    border-color: %(border_muted)s;
}
QTableWidget, QTableView {
    background: transparent;
    alternate-background-color: %(surface)s;
    border: 0;
    border-radius: 0;
    color: %(text_primary)s;
    gridline-color: transparent;
    outline: 0;
}
QTableWidget::viewport, QTableView::viewport {
    background: %(surface_alt)s;
    border: 0;
    border-radius: 0;
}
QTableWidget::item, QTableView::item {
    border: 0;
    padding: %(space_2)s %(space_3)s;
}
QTableWidget::item:hover, QTableView::item:hover {
    background: %(surface_hover)s;
}
QTableWidget::item:selected, QTableView::item:selected,
QTableWidget#ServersTable::item:selected {
    background: %(selection)s;
    border: 0;
}
QHeaderView::section {
    background: %(surface)s;
    border: 0;
    border-bottom: 1px solid %(border)s;
    color: %(text_secondary)s;
    font-weight: 700;
    padding: %(space_2)s %(space_3)s;
}
QTableWidget#ServersTable::item {
    border-bottom: 0;
}
QTableWidget#ServersTable::indicator {
    width: %(icon_size_sm)s;
    height: %(icon_size_sm)s;
    border-radius: %(radius_sm)s;
    border: 1px solid %(text_disabled)s;
    background: %(bg)s;
}
QTableWidget#ServersTable::indicator:checked {
    background: %(primary)s;
    border: 1px solid %(primary_soft)s;
    image: none;
}
QPushButton#TableActionButton {
    background: transparent;
    border: 1px solid transparent;
    border-radius: %(radius_md)s;
    color: %(text_secondary)s;
    font-size: %(text_base)s;
    font-weight: 800;
    min-height: %(control_compact_height)s;
    min-width: %(control_compact_height)s;
    padding: %(space_0)s;
}
QPushButton#TableActionButton:hover {
    background: %(surface_hover)s;
    border-color: %(primary)s;
    color: %(text_primary)s;
}
QPushButton#TableActionButton:pressed {
    background: %(surface_active)s;
}
QPushButton#TableActionButton:disabled {
    background: transparent;
    border-color: transparent;
    color: %(text_disabled)s;
}
QLabel#PagerCurrent {
    background: %(primary)s;
    border: 1px solid %(primary_border)s;
    border-radius: %(radius_md)s;
    color: %(text_inverse)s;
    font-weight: 800;
}
QPushButton#PagerButton {
    background: %(surface)s;
    border: 1px solid %(border)s;
    border-radius: %(radius_md)s;
    color: %(text_muted)s;
    font-size: %(text_base)s;
    font-weight: 800;
    min-width: %(pager_size)s;
    padding: %(space_0)s;
}
QPushButton#PagerButton:hover {
    background: %(surface_hover)s;
    border-color: %(primary)s;
    color: %(text_primary)s;
}
QPushButton#PagerButton:pressed {
    background: %(surface_active)s;
}
QPushButton#PagerButton:disabled {
    background: %(surface_muted)s;
    border-color: %(border_muted)s;
    color: %(text_disabled)s;
}
QHeaderView::section:last {
    border-right: 0;
}
QTableCornerButton::section {
    background: %(surface)s;
    border: 0;
}
QScrollBar:vertical, QScrollBar:horizontal {
    background: %(bg)s;
    border: 0;
    margin: %(space_0)s;
}
QScrollBar:vertical {
    width: %(space_3)s;
}
QScrollBar:horizontal {
    height: %(space_3)s;
}
QScrollBar::handle:vertical, QScrollBar::handle:horizontal {
    background: %(border)s;
    border-radius: %(radius_md)s;
    min-height: %(space_7)s;
    min-width: %(space_7)s;
}
QScrollBar::handle:vertical:hover, QScrollBar::handle:horizontal:hover {
    background: %(border_strong)s;
}
QScrollBar::handle:vertical:pressed, QScrollBar::handle:horizontal:pressed {
    background: %(primary)s;
}
QScrollBar::add-line, QScrollBar::sub-line {
    height: %(space_0)s;
    width: %(space_0)s;
}
QMenu {
    background: %(surface)s;
    border: 1px solid %(border)s;
    border-radius: %(radius_md)s;
    color: %(text_primary)s;
    padding: %(space_1)s;
}
QMenu::item {
    border-radius: %(radius_sm)s;
    padding: %(space_2)s %(space_5)s;
}
QMenu::item:selected {
    background: %(surface_hover)s;
}
QMenu::item:disabled {
    color: %(text_disabled)s;
}
QMessageBox, QInputDialog, QProgressDialog {
    background: %(bg)s;
    color: %(text_primary)s;
}
QMessageBox QLabel {
    background: transparent;
    color: %(text_primary)s;
}
QMessageBox QPushButton, QDialogButtonBox QPushButton {
    min-width: %(button_min_width)s;
}
QProgressBar {
    background: %(surface_alt)s;
    border: 1px solid %(border)s;
    border-radius: %(radius_md)s;
    color: %(text_primary)s;
    text-align: center;
}
QProgressBar::chunk {
    background: %(primary)s;
    border-radius: %(radius_sm)s;
}
QToolTip {
    background: %(surface)s;
    border: 1px solid %(border_strong)s;
    border-radius: %(radius_sm)s;
    color: %(text_primary)s;
    padding: %(space_1)s %(space_2)s;
}
"""
