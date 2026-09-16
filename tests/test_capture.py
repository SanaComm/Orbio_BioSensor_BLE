from orbio import capture


def test_ppg_and_accel_memories_are_separate_from_iq(tmp_path, monkeypatch):
    monkeypatch.setattr(capture, "DATA_DIR", tmp_path)
    monkeypatch.setattr(capture, "MEMORY_DIR", tmp_path / "memories")

    capture.save_memory(1, points=[{"i": 1, "q": 2, "f": 700}], kind="iq")
    capture.save_memory(1, series=[[1], [2], [], [], [], [], [], []], kind="ppg")
    capture.save_memory(1, series={"x": [9], "y": [8], "z": [7]}, kind="accel")

    assert (tmp_path / "memories" / "mem1.json").is_file()
    assert (tmp_path / "memories" / "ppg_mem1.json").is_file()
    assert (tmp_path / "memories" / "accel_mem1.json").is_file()

    iq = capture.load_memory(1, "iq")
    ppg = capture.load_memory(1, "ppg")
    accel = capture.load_memory(1, "accel")
    assert iq["points"][0]["i"] == 1
    assert ppg["series"][0] == [1.0]
    assert ppg["series"][1] == [2.0]
    assert accel["series"] == {"x": [9.0], "y": [8.0], "z": [7.0]}

    iq_slots = capture.list_memories("iq")
    ppg_slots = capture.list_memories("ppg")
    accel_slots = capture.list_memories("accel")
    assert iq_slots[0]["empty"] is False
    assert ppg_slots[0]["empty"] is False
    assert accel_slots[0]["empty"] is False
    assert ppg_slots[1]["empty"] is True
