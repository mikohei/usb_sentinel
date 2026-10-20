import pytest

from usb_sentinel_display.libs.qr_render import (
    CANVAS_PX,
    box_size_for,
    make_matrix,
    matrix_to_ppm,
    qr_version,
    render,
)

TYPICAL = (
    '{"v":2,"r":"I","s":"ABC123456789","t":"20250601T120000Z",'
    '"d":"0123456789abcdef","st":"m:P,h:P,c:S,s:S","n":0}'
)


class TestBoxSize:
    @pytest.mark.parametrize(("size", "box"), [(49, 6), (53, 5), (57, 5), (61, 4), (65, 4)])
    def test_design_table(self, size: int, box: int) -> None:
        """Values from design table 1.1 (296 divided by the side including the quiet zone)."""
        assert box_size_for(size) == box
        assert box_size_for(size, CANVAS_PX) == box

    def test_zero_size(self) -> None:
        with pytest.raises(ValueError, match="matrix"):
            box_size_for(0)

    def test_version8_box_is_five(self) -> None:
        """Even at the detail-QR limit (version 8, 49+8=57), one module is 5 px."""
        assert box_size_for(57) == 5


class TestMatrix:
    def test_typical_is_version_7(self) -> None:
        """A typical payload is version 7, with a matrix side of 45+8."""
        assert qr_version(TYPICAL) == 7
        matrix = make_matrix(TYPICAL)
        assert len(matrix) == 53
        assert all(len(row) == 53 for row in matrix)
        assert matrix[4][4] is True
        assert matrix[0][0] is False

    def test_too_large_raises(self) -> None:
        """A payload that needs version 9 or higher raises ValueError."""
        with pytest.raises(ValueError, match="version"):
            make_matrix("x" * 200)


class TestPpm:
    def test_header_and_length(self) -> None:
        matrix = [[True, False], [False, True]]
        ppm = matrix_to_ppm(matrix, 3)
        header = b"P6 6 6 255\n"
        assert ppm.startswith(header)
        assert len(ppm) == len(header) + 6 * 6 * 3

    def test_black_white_mapping(self) -> None:
        """If the top-left is True (black) the first 3 bytes are 0; if False they are 255."""
        ppm = matrix_to_ppm([[True, False], [False, True]], 1)
        body = ppm[len(b"P6 2 2 255\n") :]
        assert body == b"\x00\x00\x00\xff\xff\xff\xff\xff\xff\x00\x00\x00"

    def test_render(self) -> None:
        """A typical payload is a 53x5=265 px square."""
        ppm, side = render(TYPICAL)
        assert side == 265
        assert ppm.startswith(b"P6 265 265 255\n")
        assert len(ppm) == len(b"P6 265 265 255\n") + 265 * 265 * 3

    @pytest.mark.parametrize(("canvas_px", "side"), [(493, 477), (592, 583), (444, 424)])
    def test_render_scaled_canvas(self, canvas_px: int, side: int) -> None:
        """The module size is canvas_px // 53, so the side is 53 times that whole number."""
        ppm, got = render(TYPICAL, canvas_px=canvas_px)
        assert got == side
        header = b"P6 %d %d 255\n" % (side, side)
        assert ppm.startswith(header)
        assert len(ppm) == len(header) + side * side * 3

    def test_nearest_neighbour_by_byte_replication(self) -> None:
        """Each module is a run of box identical triplets and each row is repeated box times."""
        box = 9
        ppm = matrix_to_ppm([[True, False], [False, True]], box)
        header = b"P6 18 18 255\n"
        body = ppm[len(header) :]
        assert set(body) == {0x00, 0xFF}
        row_bytes = 2 * box * 3
        rows = [body[i : i + row_bytes] for i in range(0, len(body), row_bytes)]
        assert len(rows) == 2 * box
        assert rows[:box] == [b"\x00" * box * 3 + b"\xff" * box * 3] * box
        assert rows[box:] == [b"\xff" * box * 3 + b"\x00" * box * 3] * box

    def test_quiet_zone_is_part_of_matrix(self) -> None:
        """The 4-module quiet zone is in the matrix, so it scales with the modules."""
        matrix = make_matrix(TYPICAL)
        assert len(matrix) == 45 + 8
        for i in (0, 1, 2, 3, -4, -3, -2, -1):
            assert not any(matrix[i])
            assert not any(row[i] for row in matrix)
