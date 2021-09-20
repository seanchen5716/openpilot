from enum import IntEnum
from typing import Dict, Union, Callable, Any

from cereal import log, car
import cereal.messaging as messaging
from common.realtime import DT_CTRL
from selfdrive.config import Conversions as CV
from selfdrive.locationd.calibrationd import MIN_SPEED_FILTER

AlertSize = log.ControlsState.AlertSize
AlertStatus = log.ControlsState.AlertStatus
VisualAlert = car.CarControl.HUDControl.VisualAlert
AudibleAlert = car.CarControl.HUDControl.AudibleAlert
EventName = car.CarEvent.EventName


# Alert priorities
class Priority(IntEnum):
  LOWEST = 0
  LOWER = 1
  LOW = 2
  MID = 3
  HIGH = 4
  HIGHEST = 5


# Event types
class ET:
  ENABLE = 'enable'
  PRE_ENABLE = 'preEnable'
  NO_ENTRY = 'noEntry'
  WARNING = 'warning'
  USER_DISABLE = 'userDisable'
  SOFT_DISABLE = 'softDisable'
  IMMEDIATE_DISABLE = 'immediateDisable'
  PERMANENT = 'permanent'


# get event name from enum
EVENT_NAME = {v: k for k, v in EventName.schema.enumerants.items()}


class Events:
  def __init__(self):
    self.events = []
    self.static_events = []
    self.events_prev = dict.fromkeys(EVENTS.keys(), 0)

  @property
  def names(self):
    return self.events

  def __len__(self):
    return len(self.events)

  def add(self, event_name, static=False):
    if static:
      self.static_events.append(event_name)
    self.events.append(event_name)

  def clear(self):
    self.events_prev = {k: (v + 1 if k in self.events else 0) for k, v in self.events_prev.items()}
    self.events = self.static_events.copy()

  def any(self, event_type):
    for e in self.events:
      if event_type in EVENTS.get(e, {}).keys():
        return True
    return False

  def create_alerts(self, event_types, callback_args=None):
    if callback_args is None:
      callback_args = []

    ret = []
    for e in self.events:
      types = EVENTS[e].keys()
      for et in event_types:
        if et in types:
          alert = EVENTS[e][et]
          if not isinstance(alert, Alert):
            alert = alert(*callback_args)

          if DT_CTRL * (self.events_prev[e] + 1) >= alert.creation_delay:
            alert.alert_type = f"{EVENT_NAME[e]}/{et}"
            alert.event_type = et
            ret.append(alert)
    return ret

  def add_from_msg(self, events):
    for e in events:
      self.events.append(e.name.raw)

  def to_msg(self):
    ret = []
    for event_name in self.events:
      event = car.CarEvent.new_message()
      event.name = event_name
      for event_type in EVENTS.get(event_name, {}).keys():
        setattr(event, event_type, True)
      ret.append(event)
    return ret


class Alert:
  def __init__(self,
               alert_text_1: str,
               alert_text_2: str,
               alert_status: log.ControlsState.AlertStatus,
               alert_size: log.ControlsState.AlertSize,
               alert_priority: Priority,
               visual_alert: car.CarControl.HUDControl.VisualAlert,
               audible_alert: car.CarControl.HUDControl.AudibleAlert,
               duration_sound: float,
               duration_hud_alert: float,
               duration_text: float,
               alert_rate: float = 0.,
               creation_delay: float = 0.):

    self.alert_text_1 = alert_text_1
    self.alert_text_2 = alert_text_2
    self.alert_status = alert_status
    self.alert_size = alert_size
    self.alert_priority = alert_priority
    self.visual_alert = visual_alert
    self.audible_alert = audible_alert

    self.duration_sound = duration_sound
    self.duration_hud_alert = duration_hud_alert
    self.duration_text = duration_text

    self.alert_rate = alert_rate
    self.creation_delay = creation_delay

    self.start_time = 0.
    self.alert_type = ""
    self.event_type = None

  def __str__(self) -> str:
    return f"{self.alert_text_1}/{self.alert_text_2} {self.alert_priority} {self.visual_alert} {self.audible_alert}"

  def __gt__(self, alert2) -> bool:
    return self.alert_priority > alert2.alert_priority


class NoEntryAlert(Alert):
  def __init__(self, alert_text_2, audible_alert=AudibleAlert.chimeError,
               visual_alert=VisualAlert.none, duration_hud_alert=2.):
    super().__init__("無法啟用OPENPILOT", alert_text_2, AlertStatus.normal,
                     AlertSize.mid, Priority.LOW, visual_alert,
                     audible_alert, .4, duration_hud_alert, 3.)


class SoftDisableAlert(Alert):
  def __init__(self, alert_text_2):
    super().__init__("緊握方向盤", alert_text_2,
                     AlertStatus.critical, AlertSize.full,
                     Priority.MID, VisualAlert.steerRequired,
                     AudibleAlert.chimeError, .1, 2., 2.),


class ImmediateDisableAlert(Alert):
  def __init__(self, alert_text_2, alert_text_1="緊握方向盤"):
    super().__init__(alert_text_1, alert_text_2,
                     AlertStatus.critical, AlertSize.full,
                     Priority.HIGHEST, VisualAlert.steerRequired,
                     AudibleAlert.chimeWarningRepeat, 2.2, 3., 4.),


class EngagementAlert(Alert):
  def __init__(self, audible_alert=True):
    super().__init__("", "",
                     AlertStatus.normal, AlertSize.none,
                     Priority.MID, VisualAlert.none,
                     audible_alert, .2, 0., 0.),


class NormalPermanentAlert(Alert):
  def __init__(self, alert_text_1: str, alert_text_2: str, duration_text: float = 0.2):
    super().__init__(alert_text_1, alert_text_2,
                     AlertStatus.normal, AlertSize.mid if len(alert_text_2) else AlertSize.small,
                     Priority.LOWER, VisualAlert.none, AudibleAlert.none, 0., 0., duration_text),


# ********** alert callback functions **********
def below_steer_speed_alert(CP: car.CarParams, sm: messaging.SubMaster, metric: bool) -> Alert:
  speed = int(round(CP.minSteerSpeed * (CV.MS_TO_KPH if metric else CV.MS_TO_MPH)))
  unit = "km/h" if metric else "mph"
  return Alert(
    "緊握方向盤",
    "%d %s 以下無法進行轉向控制" % (speed, unit),
    AlertStatus.userPrompt, AlertSize.mid,
    Priority.MID, VisualAlert.none, AudibleAlert.none, 0., 0.4, .3)


def calibration_incomplete_alert(CP: car.CarParams, sm: messaging.SubMaster, metric: bool) -> Alert:
  speed = int(MIN_SPEED_FILTER * (CV.MS_TO_KPH if metric else CV.MS_TO_MPH))
  unit = "km/h" if metric else "mph"
  return Alert(
    "正在進行校正: %d%%" % sm['liveCalibration'].calPerc,
    "速度需高於%d %s" % (speed, unit),
    AlertStatus.normal, AlertSize.mid,
    Priority.LOWEST, VisualAlert.none, AudibleAlert.none, 0., 0., .2)


def no_gps_alert(CP: car.CarParams, sm: messaging.SubMaster, metric: bool) -> Alert:
  gps_integrated = sm['pandaState'].pandaType in [log.PandaState.PandaType.uno, log.PandaState.PandaType.dos]
  return Alert(
    "GPS訊號微弱",
    "若使用環境沒有問題，請聯絡comma support" if gps_integrated else "請檢查GPS接收器位置",
    AlertStatus.normal, AlertSize.mid,
    Priority.LOWER, VisualAlert.none, AudibleAlert.none, 0., 0., .2, creation_delay=300.)


def wrong_car_mode_alert(CP: car.CarParams, sm: messaging.SubMaster, metric: bool) -> Alert:
  text = "關閉巡航模式"
  if CP.carName == "honda":
    text = "Main Switch Off"
  return NoEntryAlert(text, duration_hud_alert=0.)


def startup_fuzzy_fingerprint_alert(CP: car.CarParams, sm: messaging.SubMaster, metric: bool) -> Alert:
  return Alert(
    "與車輛指紋不匹配",
    f"相似的指紋: {CP.carFingerprint.title()[:40]}",
    AlertStatus.userPrompt, AlertSize.mid,
    Priority.LOWER, VisualAlert.none, AudibleAlert.none, 0., 0., 15.)


def joystick_alert(CP: car.CarParams, sm: messaging.SubMaster, metric: bool) -> Alert:
  axes = sm['testJoystick'].axes
  gb, steer = list(axes)[:2] if len(axes) else (0., 0.)
  return Alert(
    "Joystick Mode",
    f"Gas: {round(gb * 100.)}%, Steer: {round(steer * 100.)}%",
    AlertStatus.normal, AlertSize.mid,
    Priority.LOW, VisualAlert.none, AudibleAlert.none, 0., 0., .1)


def standstill_alert(CP, sm, metric):
  elapsed_time = sm['pathPlan'].standstillElapsedTime
  elapsed_time_min = elapsed_time // 60
  elapsed_time_sec = elapsed_time - (elapsed_time_min * 60)

  if elapsed_time_min == 0:
    return Alert(
      "暫停 (經過時間: %02d秒)" % (elapsed_time_sec),
      "",
      AlertStatus.normal, AlertSize.small,
      Priority.LOW, VisualAlert.none, AudibleAlert.none, .0, .1, .1, alert_rate=0.5)
  else:
    return Alert(
      "暫停 (經過時間: %d分 %02d秒)" % (elapsed_time_min, elapsed_time_sec),
      "",
      AlertStatus.normal, AlertSize.small,
      Priority.LOW, VisualAlert.none, AudibleAlert.none, .0, .1, .1, alert_rate=0.5)

EVENTS: Dict[int, Dict[str, Union[Alert, Callable[[Any, messaging.SubMaster, bool], Alert]]]] = {
  # ********** events with no alerts **********

  EventName.stockFcw: {},

  # ********** events only containing alerts displayed in all states **********

  EventName.modelLongAlert: {
    ET.PERMANENT: Alert(
      "MODEL LONG已啟用",
      "請注音駕駛，避免發生意外狀況",
      AlertStatus.normal, AlertSize.mid,
      Priority.LOW, VisualAlert.none, AudibleAlert.chimeWarning1, .4, 0., 2.),
  },

  EventName.joystickDebug: {
    ET.WARNING: joystick_alert,
    ET.PERMANENT: Alert(
      "Joystick Mode",
      "",
      AlertStatus.normal, AlertSize.small,
      Priority.LOWER, VisualAlert.none, AudibleAlert.none, 0., 0., 0.1),
  },

  EventName.controlsInitializing: {
    ET.NO_ENTRY: NoEntryAlert("控制處理器初始化..."),
  },

  EventName.startup: {
    ET.PERMANENT: Alert(
      "OPENPILOT已準備完成",
      "請緊握方向盤並注意路況，以確保安全",
      AlertStatus.normal, AlertSize.mid,
      Priority.LOWER, VisualAlert.none, AudibleAlert.none, 0., 0., 5.),
  },

  EventName.startupMaster: {
    ET.PERMANENT: Alert(
      "警告:此分支尚未經過測試",
      "為了能安全駕駛，請全程緊握方向盤並注意路況",
      AlertStatus.userPrompt, AlertSize.mid,
      Priority.LOWER, VisualAlert.none, AudibleAlert.none, 0., 0., 5.),
  },

  # Car is recognized, but marked as dashcam only
  EventName.startupNoControl: {
    ET.PERMANENT: Alert(
      "行車記錄器模式",
      "為了能安全駕駛，請全程緊握方向盤並注意路況",
      AlertStatus.normal, AlertSize.mid,
      Priority.LOWER, VisualAlert.none, AudibleAlert.none, 0., 0., 5.),
  },

  # Car is not recognized
  EventName.startupNoCar: {
    ET.PERMANENT: Alert(
      "行車記錄器模式:不支援的車輛",
      "請緊握方向盤並注意路況，以確保安全",
      AlertStatus.normal, AlertSize.mid,
      Priority.LOWER, VisualAlert.none, AudibleAlert.none, 0., 0., 5.),
  },

  # openpilot uses the version strings from various ECUs to detect the correct car model.
  # Usually all ECUs are recognized and an exact match to a car model can be made. Sometimes
  # one or two ECUs have unrecognized versions, but the others are present in the database.
  # If openpilot is confident about the match to a car model, it fingerprints anyway.
  # In this case an alert is thrown since there is a small chance the wrong car was detected
  # and the user should pay extra attention.
  # This alert can be prevented by adding all ECU firmware version to openpilot:
  # https://github.com/commaai/openpilot/wiki/Fingerprinting
  EventName.startupFuzzyFingerprint: {
    ET.PERMANENT: startup_fuzzy_fingerprint_alert,
  },

  EventName.startupNoFw: {
    ET.PERMANENT: Alert(
      "車輛無法識別",
      "檢查所有線路連接",
      AlertStatus.userPrompt, AlertSize.mid,
      Priority.LOWER, VisualAlert.none, AudibleAlert.none, 0., 0., 15.),
  },

  EventName.dashcamMode: {
    ET.PERMANENT: Alert(
      "行車記錄器模式",
      "",
      AlertStatus.normal, AlertSize.small,
      Priority.LOWEST, VisualAlert.none, AudibleAlert.none, 0., 0., .2),
  },

  EventName.invalidLkasSetting: {
    ET.PERMANENT: Alert(
      "車輛的LKAS功能已開啟",
      "關閉LKAS功能以啟用OPENPILOT",
      AlertStatus.normal, AlertSize.mid,
      Priority.LOWER, VisualAlert.none, AudibleAlert.none, 0., 0., .2),
  },

  # Some features or cars are marked as community features. If openpilot
  # detects the use of a community feature it switches to dashcam mode
  # until these features are allowed using a toggle in settings.
  EventName.communityFeatureDisallowed: {
    # LOW priority to overcome Cruise Error
    ET.PERMANENT: Alert(
      "偵測到社區功能",
      "在設定畫面中開啟社區功能",
      AlertStatus.normal, AlertSize.mid,
      Priority.LOW, VisualAlert.none, AudibleAlert.none, 0., 0., .2),
  },

  # openpilot doesn't recognize the car. This switches openpilot into a
  # read-only mode. This can be solved by adding your fingerprint.
  # See https://github.com/commaai/openpilot/wiki/Fingerprinting for more information
  EventName.carUnrecognized: {
    ET.PERMANENT: Alert(
      "行車記錄器模式",
      "無法辨識車輛",
      AlertStatus.normal, AlertSize.mid,
      Priority.LOWEST, VisualAlert.none, AudibleAlert.none, 0., 0., .2),
  },

  EventName.stockAeb: {
    ET.PERMANENT: Alert(
      "踩剎車！",
      "原車AEB:前方有碰撞危險",
      AlertStatus.critical, AlertSize.full,
      Priority.HIGHEST, VisualAlert.fcw, AudibleAlert.none, 1., 2., 2.),
    ET.NO_ENTRY: NoEntryAlert("原車AEB:前方有碰撞危險"),
  },

  EventName.fcw: {
    ET.PERMANENT: Alert(
      "踩剎車！",
      "前方有碰撞危險",
      AlertStatus.critical, AlertSize.full,
      Priority.HIGHEST, VisualAlert.fcw, AudibleAlert.chimeWarningRepeat, 1., 2., 2.),
  },

  EventName.ldw: {
    ET.PERMANENT: Alert(
      "請緊握方向盤",
      "偵測到車輛已偏離車道",
      AlertStatus.userPrompt, AlertSize.mid,
      Priority.LOW, VisualAlert.ldw, AudibleAlert.chimePrompt, 1., 2., 3.),
  },

  # ********** events only containing alerts that display while engaged **********

  EventName.gasPressed: {
    ET.PRE_ENABLE: Alert(
      "加速中OPENPILOT無法踩剎車",
      "",
      AlertStatus.normal, AlertSize.small,
      Priority.LOWEST, VisualAlert.none, AudibleAlert.none, .0, .0, .1, creation_delay=1.),
  },

  # openpilot tries to learn certain parameters about your car by observing
  # how the car behaves to steering inputs from both human and openpilot driving.
  # This includes:
  # - steer ratio: gear ratio of the steering rack. Steering angle divided by tire angle
  # - tire stiffness: how much grip your tires have
  # - angle offset: most steering angle sensors are offset and measure a non zero angle when driving straight
  # This alert is thrown when any of these values exceed a sanity check. This can be caused by
  # bad alignment or bad sensor data. If this happens consistently consider creating an issue on GitHub
  EventName.vehicleModelInvalid: {
    ET.NO_ENTRY: NoEntryAlert("車輛參數辨識失敗"),
    ET.SOFT_DISABLE: SoftDisableAlert("車輛參數辨識失敗"),
    ET.WARNING: Alert(
      "無法辨識車輛參數",
      "",
      AlertStatus.normal, AlertSize.small,
      Priority.LOWEST, VisualAlert.none, AudibleAlert.none, .0, .0, .1),
  },

  EventName.steerTempUnavailableSilent: {
    ET.WARNING: Alert(
      "轉向控制暫時被禁用",
      "",
      AlertStatus.userPrompt, AlertSize.small,
      Priority.LOW, VisualAlert.steerRequired, AudibleAlert.chimePrompt, 1., 1., 1.),
  },

  EventName.preDriverDistracted: {
    ET.WARNING: Alert(
      "注意路況",
      "",
      AlertStatus.normal, AlertSize.small,
      Priority.LOW, VisualAlert.none, AudibleAlert.none, .0, .1, .1, alert_rate=0.75),
  },

  EventName.promptDriverDistracted: {
    ET.WARNING: Alert(
      "請注意路況",
      "請注視前方",
      AlertStatus.userPrompt, AlertSize.mid,
      Priority.MID, VisualAlert.steerRequired, AudibleAlert.chimeWarning2Repeat, .1, .1, .1),
  },

  EventName.driverDistracted: {
    ET.WARNING: Alert(
      "警告:將解除方向盤控制",
      "駕駛未注視前方",
      AlertStatus.critical, AlertSize.full,
      Priority.HIGH, VisualAlert.steerRequired, AudibleAlert.chimeWarningRepeat, .1, .1, .1),
  },

  EventName.preDriverUnresponsive: {
    ET.WARNING: Alert(
      "緊握方向盤:無監控",
      "",
      AlertStatus.normal, AlertSize.small,
      Priority.LOW, VisualAlert.none, AudibleAlert.none, .0, .1, .1, alert_rate=0.75),
  },

  EventName.promptDriverUnresponsive: {
    ET.WARNING: Alert(
      "緊握方向盤",
      "無監控駕駛",
      AlertStatus.userPrompt, AlertSize.mid,
      Priority.MID, VisualAlert.none, AudibleAlert.none, .1, .1, .1),
  },

  EventName.driverUnresponsive: {
    ET.WARNING: Alert(
      "警告:將解除方向盤控制",
      "不監控駕駛",
      AlertStatus.critical, AlertSize.full,
      Priority.HIGH, VisualAlert.none, AudibleAlert.none, .1, .1, .1),
  },

  EventName.manualRestart: {
    ET.WARNING: Alert(
      "緊握方向盤",
      "請手動重新開機",
      AlertStatus.userPrompt, AlertSize.mid,
      Priority.LOW, VisualAlert.none, AudibleAlert.none, 0., 0., .2),
  },

  EventName.resumeRequired: {
    ET.WARNING: Alert(
      "暫停",
      "按RES鈕重新啟動",
      AlertStatus.userPrompt, AlertSize.mid,
      Priority.LOW, VisualAlert.none, AudibleAlert.none, 0., 0., .2),
  },

  EventName.belowSteerSpeed: {
    ET.WARNING: below_steer_speed_alert,
  },

  EventName.preLaneChangeLeft: {
    ET.WARNING: Alert(
      "請注意周遭車輛，將方向盤向左轉以變換車道",
      "",
      AlertStatus.normal, AlertSize.small,
      Priority.LOW, VisualAlert.none, AudibleAlert.none, .0, .1, .1, alert_rate=0.75),
  },

  EventName.preLaneChangeRight: {
    ET.WARNING: Alert(
      "請注意周遭車輛，將方向盤向右轉以變換車道",
      "",
      AlertStatus.normal, AlertSize.small,
      Priority.LOW, VisualAlert.none, AudibleAlert.none, .0, .1, .1, alert_rate=0.75),
  },

  EventName.laneChangeBlocked: {
    ET.WARNING: Alert(
      "後方來車",
      "",
      AlertStatus.userPrompt, AlertSize.small,
      Priority.LOW, VisualAlert.none, AudibleAlert.chimePrompt, .1, .1, .1),
  },

  EventName.laneChange: {
    ET.WARNING: Alert(
      "變換車道中",
      "",
      AlertStatus.normal, AlertSize.small,
      Priority.LOW, VisualAlert.none, AudibleAlert.none, .0, .1, .1),
  },
  
  EventName.laneChangeManual: {
    ET.WARNING: Alert(
      "低速行駛中，請手動轉方向盤,
      "暫停自動變換車道功能，請自行駕駛",
      AlertStatus.userPrompt, AlertSize.mid,
      Priority.LOW, VisualAlert.none, AudibleAlert.none, .0, .1, .1, alert_rate=0.75),
  },

  EventName.emgButtonManual: {
    ET.WARNING: Alert(
      "雙黃燈閃爍",
      "",
      AlertStatus.userPrompt, AlertSize.small,
      Priority.LOW, VisualAlert.none, AudibleAlert.none, .0, .1, .1, alert_rate=0.75),
  },

  EventName.driverSteering: {
    ET.WARNING: Alert(
      "請手動駕駛",
      "自動轉向功能暫時失靈",
      AlertStatus.userPrompt, AlertSize.mid,
      Priority.LOW, VisualAlert.none, AudibleAlert.none, .0, .1, .1),
  },

  EventName.steerSaturated: {
    ET.WARNING: Alert(
      "緊握方向盤",
      "已超出車道維持範圍",
      AlertStatus.userPrompt, AlertSize.mid,
      Priority.LOW, VisualAlert.none, AudibleAlert.none, .1, .1, .1),
  },

  # Thrown when the fan is driven at >50% but is not rotating
  EventName.fanMalfunction: {
    ET.PERMANENT: NormalPermanentAlert("風扇故障", "聯絡comma support"),
  },

  # Camera is not outputting frames at a constant framerate
  EventName.cameraMalfunction: {
    ET.PERMANENT: NormalPermanentAlert("鏡頭故障", "聯絡comma support"),
  },

  # Unused
  EventName.gpsMalfunction: {
    ET.PERMANENT: NormalPermanentAlert("GPS故障", "聯絡comma support"),
  },

  # When the GPS position and localizer diverge the localizer is reset to the
  # current GPS position. This alert is thrown when the localizer is reset
  # more often than expected.
  EventName.localizerMalfunction: {
    ET.PERMANENT: NormalPermanentAlert("傳感器故障", "聯絡comma support"),
  },

  EventName.modeChangeOpenpilot: {
    ET.WARNING: Alert(
      "啟動OPENPILOT",
      "",
      AlertStatus.normal, AlertSize.small,
      Priority.LOW, VisualAlert.none, AudibleAlert.chimeWarning1, 1., 0, 1.),
  },
  
  EventName.modeChangeDistcurv: {
    ET.WARNING: Alert(
      "啟用速度+車距",
      "",
      AlertStatus.normal, AlertSize.small,
      Priority.LOW, VisualAlert.none, AudibleAlert.chimeWarning1, 1., 0, 1.),
  },
  EventName.modeChangeDistance: {
    ET.WARNING: Alert(
      "啟用僅速度",
      "",
      AlertStatus.normal, AlertSize.small,
      Priority.LOW, VisualAlert.none, AudibleAlert.chimeWarning1, 1., 0, 1.),
  },
  EventName.modeChangeCurv: {
    ET.WARNING: Alert(
      "啟用彎道控制",
      "",
      AlertStatus.normal, AlertSize.small,
      Priority.LOW, VisualAlert.none, AudibleAlert.chimeWarning1, 1., 0, 1.),
  },
  EventName.modeChangeOneway: {
    ET.WARNING: Alert(
      "單向1車道",
      "",
      AlertStatus.normal, AlertSize.small,
      Priority.LOW, VisualAlert.none, AudibleAlert.chimeWarning1, 1., 0, 1.),
  },
  EventName.modeChangeMaponly: {
    ET.WARNING: Alert(
      "根據Tmap",
      "",
      AlertStatus.normal, AlertSize.small,
      Priority.LOW, VisualAlert.none, AudibleAlert.chimeWarning1, 1., 0, 1.),
  },
  EventName.needBrake: {
    ET.WARNING: Alert(
      "踩剎車！",
      "前方有碰撞危險",
      AlertStatus.normal, AlertSize.full,
      Priority.LOW, VisualAlert.none, AudibleAlert.chimeWarning2Repeat, .1, .1, .1),
  },
  EventName.standStill: {
    ET.WARNING: standstill_alert,
  },

  # ********** events that affect controls state transitions **********

  EventName.pcmEnable: {
    ET.ENABLE: EngagementAlert(AudibleAlert.chimeEngage),
  },

  EventName.buttonEnable: {
    ET.ENABLE: EngagementAlert(AudibleAlert.chimeEngage),
  },

  EventName.pcmDisable: {
    ET.USER_DISABLE: EngagementAlert(AudibleAlert.chimeDisengage),
  },

  EventName.buttonCancel: {
    ET.USER_DISABLE: EngagementAlert(AudibleAlert.chimeDisengage),
  },

  EventName.brakeHold: {
    ET.USER_DISABLE: EngagementAlert(AudibleAlert.none),
    ET.NO_ENTRY: NoEntryAlert("Brake Hold"),
  },

  EventName.parkBrake: {
    ET.USER_DISABLE: EngagementAlert(AudibleAlert.none),
    ET.NO_ENTRY: NoEntryAlert("파킹브레이크 체결 됨"),
  },

  EventName.pedalPressed: {
    ET.USER_DISABLE: EngagementAlert(AudibleAlert.none),
    ET.NO_ENTRY: NoEntryAlert("駐車剎車已啟動",
                              visual_alert=VisualAlert.brakePressed),
  },

  EventName.wrongCarMode: {
    ET.USER_DISABLE: EngagementAlert(AudibleAlert.chimeDisengage),
    ET.NO_ENTRY: wrong_car_mode_alert,
  },

  EventName.wrongCruiseMode: {
    ET.USER_DISABLE: EngagementAlert(AudibleAlert.none),
    ET.NO_ENTRY: NoEntryAlert("請啟動定速巡航"),
  },

  EventName.steerTempUnavailable: {
    ET.SOFT_DISABLE: SoftDisableAlert("暫時禁用轉向控制"),
    ET.NO_ENTRY: NoEntryAlert("轉向控制已被暫時禁用",
                              duration_hud_alert=0.),
  },
  
  EventName.isgActive: {
    ET.WARNING: Alert(
      "ISG啟動中，暫時禁用轉向控制",
      "",
      AlertStatus.normal, AlertSize.small,
      Priority.LOW, VisualAlert.none, AudibleAlert.none, .0, .1, .1, alert_rate=0.75),
  },

  EventName.camSpeedDown: {
    ET.WARNING: Alert(
      "安全區間減速控制",
      "",
      AlertStatus.normal, AlertSize.small,
      Priority.LOW, VisualAlert.none, AudibleAlert.none, .5, .5, .5, alert_rate=0.75),
  },

  EventName.gapAdjusting: {
    ET.WARNING: Alert(
      "調整巡航間隙",
      "",
      AlertStatus.normal, AlertSize.small,
      Priority.LOW, VisualAlert.none, AudibleAlert.none, .0, .1, .1, alert_rate=0.75),
  },

  EventName.outOfSpace: {
    ET.PERMANENT: Alert(
      "硬碟空間不足",
      "",
      AlertStatus.normal, AlertSize.small,
      Priority.LOWER, VisualAlert.none, AudibleAlert.none, 0., 0., .2),
    ET.NO_ENTRY: NoEntryAlert("Out of Storage Space",
                              duration_hud_alert=0.),
  },

  EventName.belowEngageSpeed: {
    ET.NO_ENTRY: NoEntryAlert("車速過低"),
  },

  EventName.sensorDataInvalid: {
    ET.PERMANENT: Alert(
      "無法從C2傳感器上取得數據",
      "請重新開機",
      AlertStatus.normal, AlertSize.mid,
      Priority.LOWER, VisualAlert.none, AudibleAlert.none, 0., 0., .2, creation_delay=1.),
    ET.NO_ENTRY: NoEntryAlert("無法從C2傳感器上取得數據"),
  },

  EventName.noGps: {
    ET.PERMANENT: no_gps_alert,
  },

  EventName.soundsUnavailable: {
    ET.PERMANENT: NormalPermanentAlert("找不到喇叭", "請重新開機"),
    ET.NO_ENTRY: NoEntryAlert("找不到喇叭"),
  },

  EventName.tooDistracted: {
    ET.NO_ENTRY: NoEntryAlert("駕駛分心"),
  },

  EventName.overheat: {
    ET.PERMANENT: Alert(
      "系統過熱",
      "",
      AlertStatus.normal, AlertSize.small,
      Priority.LOWER, VisualAlert.none, AudibleAlert.none, 0., 0., .2),
    ET.SOFT_DISABLE: SoftDisableAlert("系統過熱"),
    ET.NO_ENTRY: NoEntryAlert("系統過熱"),
  },

  EventName.wrongGear: {
    ET.USER_DISABLE: EngagementAlert(AudibleAlert.chimeDisengage),
    ET.NO_ENTRY: NoEntryAlert("將檔位打至D檔"),
  },

  # This alert is thrown when the calibration angles are outside of the acceptable range.
  # For example if the device is pointed too much to the left or the right.
  # Usually this can only be solved by removing the mount from the windshield completely,
  # and attaching while making sure the device is pointed straight forward and is level.
  # See https://comma.ai/setup for more information
  EventName.calibrationInvalid: {
    ET.PERMANENT: NormalPermanentAlert("校準無效", "請重新固定C2並重新校正"),
    ET.SOFT_DISABLE: SoftDisableAlert("校準無效:請重新固定C2並重新校正"),
    ET.NO_ENTRY: NoEntryAlert("校準無效:請重新固定C2並重新校正"),
  },

  EventName.calibrationIncomplete: {
    ET.PERMANENT: calibration_incomplete_alert,
    ET.SOFT_DISABLE: SoftDisableAlert("正在進行校正"),
    ET.NO_ENTRY: NoEntryAlert("正在進行校正"),
  },

  EventName.doorOpen: {
    ET.SOFT_DISABLE: SoftDisableAlert("門未關"),
    ET.NO_ENTRY: NoEntryAlert("門未關"),
  },

  EventName.seatbeltNotLatched: {
    ET.SOFT_DISABLE: SoftDisableAlert("請繫安全帶"),
    ET.NO_ENTRY: NoEntryAlert("請繫安全帶"),
  },

  EventName.espDisabled: {
    ET.SOFT_DISABLE: SoftDisableAlert("ESP關閉"),
    ET.NO_ENTRY: NoEntryAlert("ESP關閉"),
  },

  EventName.lowBattery: {
    ET.SOFT_DISABLE: SoftDisableAlert("低電量"),
    ET.NO_ENTRY: NoEntryAlert("低電量"),
  },

  # Different openpilot services communicate between each other at a certain
  # interval. If communication does not follow the regular schedule this alert
  # is thrown. This can mean a service crashed, did not broadcast a message for
  # ten times the regular interval, or the average interval is more than 10% too high.
  EventName.commIssue: {
    ET.WARNING: Alert(
      "緊握方向盤",
      "process之間通訊異常",
      AlertStatus.userPrompt, AlertSize.mid,
      Priority.LOW, VisualAlert.none, AudibleAlert.chimePrompt, 1., 1., 1.),
    #ET.SOFT_DISABLE: SoftDisableAlert("프로세스 간 통신 오류가 있습니다"),
    #ET.NO_ENTRY: NoEntryAlert("프로세스 간 통신 오류가 있습니다",
    #                          audible_alert=AudibleAlert.none),
  },

  # Thrown when manager detects a service exited unexpectedly while driving
  EventName.processNotRunning: {
    ET.NO_ENTRY: NoEntryAlert("系統故障:請重新開機",
                              audible_alert=AudibleAlert.none),
  },

  EventName.radarFault: {
    ET.SOFT_DISABLE: SoftDisableAlert("雷達異常:請重新發動汽車"),
    ET.NO_ENTRY: NoEntryAlert("雷達異常:請重新發動汽車"),
  },

  # Every frame from the camera should be processed by the model. If modeld
  # is not processing frames fast enough they have to be dropped. This alert is
  # thrown when over 20% of frames are dropped.
  EventName.modeldLagging: {
    ET.SOFT_DISABLE: SoftDisableAlert("駕駛model運算發生延遲"),
    ET.NO_ENTRY: NoEntryAlert("駕駛model運算發生延遲"),
  },

  # Besides predicting the path, lane lines and lead car data the model also
  # predicts the current velocity and rotation speed of the car. If the model is
  # very uncertain about the current velocity while the car is moving, this
  # usually means the model has trouble understanding the scene. This is used
  # as a heuristic to warn the driver.
  EventName.posenetInvalid: {
    ET.SOFT_DISABLE: SoftDisableAlert("前方影像識別異常"),
    ET.NO_ENTRY: NoEntryAlert("前方影像識別異常"),
  },

  # When the localizer detects an acceleration of more than 40 m/s^2 (~4G) we
  # alert the driver the device might have fallen from the windshield.
  EventName.deviceFalling: {
    ET.SOFT_DISABLE: SoftDisableAlert("C2設備連結不穩定"),
    ET.NO_ENTRY: NoEntryAlert("C2設備連結不穩定"),
  },

  EventName.lowMemory: {
    ET.SOFT_DISABLE: SoftDisableAlert("記憶體不足:請重新開機"),
    ET.PERMANENT: NormalPermanentAlert("記憶體不足", "請重新開機"),
    ET.NO_ENTRY: NoEntryAlert("記憶體不足:請重新開機",
                               audible_alert=AudibleAlert.chimeDisengage),
  },

  EventName.highCpuUsage: {
    #ET.SOFT_DISABLE: SoftDisableAlert("System Malfunction: Reboot Your Device"),
    #ET.PERMANENT: NormalPermanentAlert("System Malfunction", "Reboot your Device"),
    ET.NO_ENTRY: NoEntryAlert("系統故障：請重新開機",
                              audible_alert=AudibleAlert.chimeDisengage),
  },

  EventName.accFaulted: {
    ET.IMMEDIATE_DISABLE: ImmediateDisableAlert("定速巡航異常"),
    ET.PERMANENT: NormalPermanentAlert("定速巡航異常", ""),
    ET.NO_ENTRY: NoEntryAlert("定速巡航異常"),
  },

  EventName.controlsMismatch: {
    ET.IMMEDIATE_DISABLE: ImmediateDisableAlert("Controls Mismatch"),
  },

  EventName.roadCameraError: {
    ET.PERMANENT: NormalPermanentAlert("前方鏡頭錯誤", "",
                                       duration_text=10.),
  },

  EventName.driverCameraError: {
    ET.PERMANENT: NormalPermanentAlert("駕駛員鏡頭錯誤", "",
                                       duration_text=10.),
  },

  EventName.wideRoadCameraError: {
    ET.PERMANENT: NormalPermanentAlert("前方魚眼鏡頭錯誤", "",
                                       duration_text=10.),
  },

  # Sometimes the USB stack on the device can get into a bad state
  # causing the connection to the panda to be lost
  EventName.usbError: {
    ET.SOFT_DISABLE: SoftDisableAlert("USB錯誤:請重新開機"),
    ET.PERMANENT: NormalPermanentAlert("USB錯誤:請重新開機", ""),
    ET.NO_ENTRY: NoEntryAlert("USB錯誤:請重新開機"),
  },

  # This alert can be thrown for the following reasons:
  # - No CAN data received at all
  # - CAN data is received, but some message are not received at the right frequency
  # If you're not writing a new car port, this is usually cause by faulty wiring
  EventName.canError: {
    ET.IMMEDIATE_DISABLE: ImmediateDisableAlert("CAN異常:檢查CAN訊號"),
    ET.PERMANENT: Alert(
      "CAN異常:檢查CAN訊號",
      "",
      AlertStatus.normal, AlertSize.small,
      Priority.LOW, VisualAlert.none, AudibleAlert.none, 0., 0., .2, creation_delay=1.),
    ET.NO_ENTRY: NoEntryAlert("CAN異常:檢查CAN訊號"),
  },

  EventName.steerUnavailable: {
    ET.IMMEDIATE_DISABLE: ImmediateDisableAlert("LKAS異常:請重新發動汽車"),
    ET.PERMANENT: Alert(
      "LKAS異常:請重新發動汽車",
      "",
      AlertStatus.normal, AlertSize.small,
      Priority.LOWER, VisualAlert.none, AudibleAlert.none, 0., 0., .2),
    ET.NO_ENTRY: NoEntryAlert("LKAS異常:請重新發動汽車"),
  },

  EventName.brakeUnavailable: {
    ET.IMMEDIATE_DISABLE: ImmediateDisableAlert("定速巡航異常: 請重新發動汽車"),
    ET.PERMANENT: Alert(
      "定速巡航異常: 請重新發動汽車",
      "",
      AlertStatus.normal, AlertSize.small,
      Priority.LOWER, VisualAlert.none, AudibleAlert.none, 0., 0., .2),
    ET.NO_ENTRY: NoEntryAlert("定速巡航異常: 請重新發動汽車"),
  },

  EventName.reverseGear: {
    ET.PERMANENT: Alert(
      "倒車檔",
      "",
      AlertStatus.normal, AlertSize.full,
      Priority.LOWEST, VisualAlert.none, AudibleAlert.none, 0., 0., .2, creation_delay=0.5),
    ET.IMMEDIATE_DISABLE: ImmediateDisableAlert("倒車檔"),
    ET.NO_ENTRY: NoEntryAlert("倒車檔"),
  },

  # On cars that use stock ACC the car can decide to cancel ACC for various reasons.
  # When this happens we can no long control the car so the user needs to be warned immediately.
  EventName.cruiseDisabled: {
    ET.IMMEDIATE_DISABLE: ImmediateDisableAlert("關閉定速巡航"),
  },

  # For planning the trajectory Model Predictive Control (MPC) is used. This is
  # an optimization algorithm that is not guaranteed to find a feasible solution.
  # If no solution is found or the solution has a very high cost this alert is thrown.
  EventName.plannerError: {
    ET.IMMEDIATE_DISABLE: ImmediateDisableAlert("Planner Solution Error"),
    ET.NO_ENTRY: NoEntryAlert("Planner Solution Error"),
  },

  # When the relay in the harness box opens the CAN bus between the LKAS camera
  # and the rest of the car is separated. When messages from the LKAS camera
  # are received on the car side this usually means the relay hasn't opened correctly
  # and this alert is thrown.
  EventName.relayMalfunction: {
    ET.IMMEDIATE_DISABLE: ImmediateDisableAlert("線束異常"),
    ET.PERMANENT: NormalPermanentAlert("線束異常", "請檢查C2設備"),
    ET.NO_ENTRY: NoEntryAlert("하네스 오작동"),
  },

  EventName.noTarget: {
    ET.IMMEDIATE_DISABLE: Alert(
      "無法啟動OPENPILOT",
      "前方無車輛",
      AlertStatus.normal, AlertSize.mid,
      Priority.HIGH, VisualAlert.none, AudibleAlert.none, .4, 2., 3.),
    ET.NO_ENTRY: NoEntryAlert("前方無車輛"),
  },

  EventName.speedTooLow: {
    ET.IMMEDIATE_DISABLE: Alert(
      "無法啟動OPENPILOT",
      "汽車低速行駛中",
      AlertStatus.normal, AlertSize.mid,
      Priority.HIGH, VisualAlert.none, AudibleAlert.none, .4, 2., 3.),
  },

  # When the car is driving faster than most cars in the training data the model outputs can be unpredictable
  EventName.speedTooHigh: {
    ET.WARNING: Alert(
      "速度過快",
      "請降低速度以恢復運作",
      AlertStatus.normal, AlertSize.mid,
      Priority.HIGH, VisualAlert.none, AudibleAlert.chimeWarning2Repeat, 2.2, 3., 4.),
    ET.NO_ENTRY: Alert(
      "速度過快",
      "請降低速度以恢復運作",
      AlertStatus.normal, AlertSize.mid,
      Priority.LOW, VisualAlert.none, AudibleAlert.chimeError, .4, 2., 3.),
  },

  EventName.lowSpeedLockout: {
    ET.PERMANENT: Alert(
      "定速巡航異常:請重新發動汽車",
      "",
      AlertStatus.normal, AlertSize.small,
      Priority.LOWER, VisualAlert.none, AudibleAlert.none, 0., 0., .2),
    ET.NO_ENTRY: NoEntryAlert("定速巡航異常:請重新發動汽車"),
  },

}
