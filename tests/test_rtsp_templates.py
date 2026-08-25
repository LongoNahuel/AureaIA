from __future__ import annotations

from aurea_vms.core.rtsp_templates import DEVICE_TYPE_LABELS, DEVICE_TYPES, build_rtsp_urls


def test_ipc_usa_patron_media_video():
    main, sub = build_rtsp_urls("ipc", "192.168.1.10", 554)
    assert main == "rtsp://192.168.1.10:554/media/video1"
    assert sub == "rtsp://192.168.1.10:554/media/video2"


def test_nvr_usa_patron_unicast_por_canal():
    main, sub = build_rtsp_urls("nvr", "10.0.0.5", 554, channel=7)
    assert main == "rtsp://10.0.0.5:554/unicast/c7/s0/live"
    assert sub == "rtsp://10.0.0.5:554/unicast/c7/s1/live"


def test_xvr_mismo_patron_que_nvr():
    assert build_rtsp_urls("xvr", "10.0.0.5", 554, channel=2) == build_rtsp_urls(
        "nvr", "10.0.0.5", 554, channel=2
    )


def test_todos_los_tipos_tienen_label():
    assert set(DEVICE_TYPES) == set(DEVICE_TYPE_LABELS)
