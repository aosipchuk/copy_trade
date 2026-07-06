from decimal import Decimal
from zipfile import ZipFile

from app.schemas.trader import PositionItem, TraderStatSchema
from app.services.analytics.export_workbook import (
    TraderAllTimeMetricsExportRow,
    build_hyperliquid_leaderboard_export_workbook,
    build_trader_all_time_metrics_export_workbook,
    build_trader_export_workbook,
    hyperliquid_leaderboard_export_filename,
    trader_all_time_metrics_export_filename,
    trader_export_filename,
    xlsx_media_type,
)
from app.services.hyperliquid.models import Fill, LeaderboardRow


def _fill(
    *,
    coin: str,
    direction: str,
    closed_pnl: str,
    oid: int,
    time: int,
) -> Fill:
    return Fill(
        coin=coin,
        px=Decimal("100"),
        sz=Decimal("2"),
        side="B",
        time=time,
        closedPnl=Decimal(closed_pnl),
        dir=direction,
        oid=oid,
        fee=Decimal("0.05"),
    )


def test_build_trader_export_workbook_contains_only_perp_history(tmp_path) -> None:
    workbook = build_trader_export_workbook(
        display_name="Alpha Trader",
        address="0xabc",
        stats=[
            TraderStatSchema(
                period="allTime",
                pnl_usd=23.0,
                roi_pct=11.5,
                volume_usd=400.0,
            )
        ],
        open_positions=[
            PositionItem(
                coin="BTC",
                side="long",
                size=0.25,
                entry_px=100000.0,
                unrealized_pnl=125.0,
                leverage=5,
            )
        ],
        fills=[
            _fill(
                coin="BTC",
                direction="Open Long",
                closed_pnl="0",
                oid=1,
                time=1_700_000_000_000,
            ),
            _fill(
                coin="BTC",
                direction="Open Long",
                closed_pnl="0",
                oid=1,
                time=1_700_000_001_000,
            ),
            _fill(
                coin="BTC",
                direction="Close Long",
                closed_pnl="23",
                oid=2,
                time=1_700_000_100_000,
            ),
            _fill(
                coin="BTC",
                direction="Close Long",
                closed_pnl="12",
                oid=2,
                time=1_700_000_101_000,
            ),
            _fill(
                coin="PURR",
                direction="Buy",
                closed_pnl="5",
                oid=3,
                time=1_700_000_200_000,
            ),
        ],
    )

    output = tmp_path / "export.xlsx"
    output.write_bytes(workbook)

    with ZipFile(output) as archive:
        names = set(archive.namelist())
        assert "[Content_Types].xml" in names
        assert "xl/workbook.xml" in names
        assert "xl/worksheets/sheet1.xml" in names
        assert "xl/worksheets/sheet3.xml" in names
        assert "xl/worksheets/sheet4.xml" not in names

        workbook_xml = archive.read("xl/workbook.xml").decode()
        assert "Summary" in workbook_xml
        assert "Open Positions" in workbook_xml
        assert "Grouped Trades" in workbook_xml
        assert "All Perp Fills" not in workbook_xml

        trades_xml = archive.read("xl/worksheets/sheet3.xml").decode()
        assert trades_xml.count("<row ") == 3  # header + open order + close order
        assert trades_xml.count("Open Long") == 1
        assert trades_xml.count("Close Long") == 1
        assert "PURR" not in trades_xml
        assert "Buy" not in trades_xml

        summary_xml = archive.read("xl/worksheets/sheet1.xml").decode()
        assert "Realized PnL USD" in summary_xml
        assert "Source Perp Fills" in summary_xml
        assert "<v>35.0</v>" in summary_xml


def test_export_metadata_helpers() -> None:
    assert xlsx_media_type().endswith("spreadsheetml.sheet")
    assert trader_export_filename("Trader / One", "0xabc").startswith(
        "trader_Trader_One_"
    )
    assert trader_export_filename("Trader / One", "0xabc").endswith(".xlsx")
    assert hyperliquid_leaderboard_export_filename().startswith(
        "hyperliquid_all_traders_"
    )
    assert hyperliquid_leaderboard_export_filename().endswith(".xlsx")
    assert trader_all_time_metrics_export_filename().startswith(
        "traders_alltime_metrics_"
    )
    assert trader_all_time_metrics_export_filename().endswith(".xlsx")


def test_build_hyperliquid_leaderboard_export_workbook(tmp_path) -> None:
    workbook = build_hyperliquid_leaderboard_export_workbook(
        [
            LeaderboardRow.model_validate(
                {
                    "ethAddress": "0xabc",
                    "displayName": "Alpha",
                    "accountValue": "1234.56",
                    "windowPerformances": [
                        ["day", {"pnl": "1.25", "roi": "0.10", "vlm": "100"}],
                        ["week", {"pnl": "2.50", "roi": "0.20", "vlm": "200"}],
                        ["month", {"pnl": "3.75", "roi": "0.30", "vlm": "300"}],
                        ["allTime", {"pnl": "4.00", "roi": "0.40", "vlm": "400"}],
                    ],
                }
            )
        ]
    )

    output = tmp_path / "hl.xlsx"
    output.write_bytes(workbook)

    with ZipFile(output) as archive:
        workbook_xml = archive.read("xl/workbook.xml").decode()
        assert "HL Leaderboard" in workbook_xml

        sheet_xml = archive.read("xl/worksheets/sheet1.xml").decode()
        assert "0xabc" in sheet_xml
        assert "Account Value" in sheet_xml
        assert "AllTime PNL" in sheet_xml
        assert "<v>1234.56</v>" in sheet_xml


def test_build_trader_all_time_metrics_export_workbook(tmp_path) -> None:
    workbook = build_trader_all_time_metrics_export_workbook(
        [
            TraderAllTimeMetricsExportRow(
                address="0xabc",
                trade_count=42,
                roi_pct=12.5,
                pnl_usd=3456.75,
                active_trading_days=17,
                max_drawdown_pct=8.25,
            )
        ]
    )

    output = tmp_path / "metrics.xlsx"
    output.write_bytes(workbook)

    with ZipFile(output) as archive:
        workbook_xml = archive.read("xl/workbook.xml").decode()
        assert "AllTime Metrics" in workbook_xml

        sheet_xml = archive.read("xl/worksheets/sheet1.xml").decode()
        assert "0xabc" in sheet_xml
        assert "Act D" in sheet_xml
        assert "<v>42</v>" in sheet_xml
        assert "<v>3456.75</v>" in sheet_xml
