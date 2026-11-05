from inference_vitals import sysstat

VM = """Mach Virtual Memory Statistics: (page size of 16384 bytes)
Pages free:                                   709779.
Pageins:                                    29414384.
Pageouts:                                      38007.
Swapins:                                     6912675.
Swapouts:                                   10618742.
"""


def test_vm_stat_counters_in_bytes():
    v = sysstat.parse_vm_stat(VM)
    assert v["swapped_in_bytes_total"] == 6912675 * 16384
    assert v["swapped_out_bytes_total"] == 10618742 * 16384
    assert v["paged_in_bytes_total"] == 29414384 * 16384 and v["paged_out_bytes_total"] == 38007 * 16384


def test_memory_pressure_and_swap_usage():
    assert sysstat.memory_free_percent("77\n") == 77
    assert sysstat.swap_usage("total = 3072.00M  used = 1866.19M  free = 1205.81M  (encrypted)") == (
        int(3072.00 * 2**20),
        int(1866.19 * 2**20),
    )


def test_render_has_every_number_with_fake_readers():
    s = sysstat.Sysstat(
        read=lambda cmd: {
            "vm_stat": VM,
            "kern.memorystatus_level": "77",
            "vm.swapusage": "total = 1024.00M  used = 512.00M  free = 512.00M",
        }[cmd]
    )
    text = s.render()
    for name in sysstat.METRICS:
        assert name in text, name
    assert "vitals_mac_memory_free_percent 77" in text
