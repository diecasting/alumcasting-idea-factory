"""End-to-end pipeline tests (dry-run fixtures + injected sources)."""

from pathlib import Path

from app.radar.models import RawSignal
from app.radar.pipeline import run_pipeline
from app.radar.sources.base import SourceAdapter


class FakeSource(SourceAdapter):
    source_type = "fake"

    def __init__(self, raws):
        super().__init__("fake:test")
        self._raws = raws

    def collect(self):
        return self._raws


def _raw(title, url="https://x.com/1", body=""):
    return RawSignal(
        source="fake:test", source_type="fake", external_id="1", title=title, body=body, url=url
    )


def test_dry_run_produces_all_outputs(tmp_path):
    report = run_pipeline(dry_run=True, out_dir=str(tmp_path))
    assert report.total_raw == 9
    assert report.total_relevant > 0
    for p in (
        "data/raw_signals.json",
        "data/normalized_signals.json",
        "data/deduplicated_signals.json",
        "reports/content_opportunity_report.json",
        "reports/content_opportunity_report.csv",
        "reports/content_opportunity_report.md",
    ):
        assert (tmp_path / p).exists(), f"missing output: {p}"


def test_pipeline_with_injected_sources_filters_noise(tmp_path):
    raws = [
        _raw("Why am I getting porosity in my aluminum die casting?"),
        _raw("Die casting market expected to grow to $40 billion by 2030", url="https://x.com/2"),
    ]
    report = run_pipeline(sources=[FakeSource(raws)], out_dir=str(tmp_path))
    assert report.total_raw == 2
    # Market-news signal is filtered out; the real problem remains.
    assert report.total_deduped == 1
    assert "porosity" in report.signals[0].title.lower()
