import React from "react";
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { GeocoderSearchPanel } from "../GeocoderSearchPanel";
import type { GeocoderResult } from "../geocoderClient";
import type { GeocodeCandidate, GeocodeSearchResult } from "../geocoderTypes";

const QUERY = "新北市新莊區興德路100號";

function candidate(overrides: Partial<GeocodeCandidate> = {}): GeocodeCandidate {
  return {
    candidateId: "req-1#0",
    addressRaw: QUERY,
    formattedAddress: "新北市新莊區興德路100號",
    latitude: 25.036,
    longitude: 121.45,
    precision: "rooftop",
    confidence: 0.98,
    provider: "geocode.primary_api",
    providerRequestId: "req-1",
    adminCity: "新北市",
    adminDistrict: "新莊區",
    observedAt: "2026-08-08T10:00:00+00:00",
    ...overrides,
  };
}

function searchResult(candidates: GeocodeCandidate[]): GeocodeSearchResult {
  return {
    query: QUERY,
    normalizedQuery: "新北市新莊區興德路100號",
    candidates,
    correlationId: "corr-panel-1",
    searchedAt: "2026-08-08T10:00:00.000Z",
    rejectedRowCount: 0,
  };
}

/** A search seam that resolves to whatever the test needs. */
function stubSearch(outcome: GeocoderResult<GeocodeSearchResult>) {
  return vi.fn(async () => outcome) as never;
}

function renderPanel(props: Partial<React.ComponentProps<typeof GeocoderSearchPanel>> = {}) {
  const onSelect = vi.fn();
  const onAudit = vi.fn();
  render(
    <GeocoderSearchPanel
      actorRoleId="expansion-manager"
      canSearch
      canSelect
      configuredOverride
      onAudit={onAudit}
      onSelect={onSelect}
      searchImpl={stubSearch({ ok: true, value: searchResult([candidate()]) })}
      {...props}
    />,
  );
  return { onSelect, onAudit };
}

async function runSearch(query = QUERY) {
  fireEvent.change(screen.getByTestId("geocoder-query-input"), { target: { value: query } });
  fireEvent.click(screen.getByTestId("geocoder-search-button"));
  await waitFor(() => expect(screen.getByTestId("geocoder-search-button")).toHaveTextContent("搜尋地址"));
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("address search and candidate selection", () => {
  it("renders candidates with their provider coordinates after a search", async () => {
    renderPanel();
    await runSearch();

    expect(screen.getByTestId("geocoder-candidate-list")).toBeInTheDocument();
    expect(screen.getByTestId("geocoder-candidate-coords-0")).toHaveTextContent("25.036000, 121.450000");
    expect(screen.getByTestId("geocoder-candidate-confidence-0")).toHaveTextContent("0.98");
    expect(screen.getByTestId("geocoder-result-meta")).toHaveTextContent("corr-panel-1");
  });

  it("rejects a too-short query locally without calling the provider", async () => {
    const searchImpl = stubSearch({ ok: true, value: searchResult([candidate()]) });
    renderPanel({ searchImpl });

    fireEvent.change(screen.getByTestId("geocoder-query-input"), { target: { value: "台北" } });
    fireEvent.click(screen.getByTestId("geocoder-search-button"));

    expect(searchImpl).not.toHaveBeenCalled();
  });

  it("selects a clean candidate and confirms it without a review reason", async () => {
    const { onSelect, onAudit } = renderPanel();
    await runSearch();

    expect(screen.getByTestId("geocoder-candidate-requirement-0")).toHaveTextContent("可直接採用");
    fireEvent.click(screen.getByTestId("geocoder-select-0"));
    expect(screen.queryByTestId("geocoder-review-reason")).toBeNull();

    fireEvent.click(screen.getByTestId("geocoder-confirm"));

    expect(onSelect).toHaveBeenCalledTimes(1);
    const selection = onSelect.mock.calls[0][0];
    expect(selection.candidate.latitude).toBe(25.036);
    expect(selection.assessment.requirement).toBe("auto_selectable");
    expect(onAudit).toHaveBeenCalledTimes(1);
    expect(onAudit.mock.calls[0][0].action).toBe("candidate_selected");
  });

  it("shows an empty state when the provider returns no candidates", async () => {
    renderPanel({ searchImpl: stubSearch({ ok: true, value: searchResult([]) }) });
    await runSearch();

    expect(screen.getByTestId("geocoder-empty")).toBeInTheDocument();
    expect(screen.queryByTestId("geocoder-candidate-list")).toBeNull();
  });

  it("discloses rows the provider returned without usable coordinates", async () => {
    renderPanel({
      searchImpl: stubSearch({
        ok: true,
        value: { ...searchResult([candidate()]), rejectedRowCount: 2 },
      }),
    });
    await runSearch();

    expect(screen.getByTestId("geocoder-rejected-rows")).toHaveTextContent("2 筆");
  });
});

describe("low-confidence candidates require explicit review", () => {
  it("marks a below-threshold candidate as needing review and lists the reasons", async () => {
    renderPanel({
      searchImpl: stubSearch({ ok: true, value: searchResult([candidate({ confidence: 0.42 })]) }),
    });
    await runSearch();

    expect(screen.getByTestId("geocoder-candidate-requirement-0")).toHaveTextContent("需人工覆核");
    expect(screen.getByTestId("geocoder-candidate-reasons-0")).toHaveTextContent("定位信心低於門檻");
  });

  it("blocks confirmation until the risk is acknowledged AND a reason is written", async () => {
    const { onSelect } = renderPanel({
      searchImpl: stubSearch({ ok: true, value: searchResult([candidate({ confidence: 0.42 })]) }),
    });
    await runSearch();
    fireEvent.click(screen.getByTestId("geocoder-select-0"));

    // Neither acknowledgement nor reason.
    fireEvent.click(screen.getByTestId("geocoder-confirm"));
    expect(onSelect).not.toHaveBeenCalled();
    expect(screen.getByTestId("geocoder-local-error")).toHaveTextContent("請先確認你已了解上述風險");

    // Acknowledged, but the reason is too short to be evidence.
    fireEvent.click(screen.getByTestId("geocoder-review-ack"));
    fireEvent.change(screen.getByTestId("geocoder-review-reason"), { target: { value: "確認過" } });
    fireEvent.click(screen.getByTestId("geocoder-confirm"));
    expect(onSelect).not.toHaveBeenCalled();
    expect(screen.getByTestId("geocoder-local-error")).toHaveTextContent("覆核理由必填");
  });

  it("records an override once both gates are satisfied", async () => {
    const { onSelect, onAudit } = renderPanel({
      searchImpl: stubSearch({ ok: true, value: searchResult([candidate({ confidence: 0.42 })]) }),
    });
    await runSearch();
    fireEvent.click(screen.getByTestId("geocoder-select-0"));
    fireEvent.click(screen.getByTestId("geocoder-review-ack"));
    fireEvent.change(screen.getByTestId("geocoder-review-reason"), {
      target: { value: "已於現場核對門牌與座標，確認為同一位置。" },
    });
    fireEvent.click(screen.getByTestId("geocoder-confirm"));

    expect(onSelect).toHaveBeenCalledTimes(1);
    const audit = onAudit.mock.calls[0][0];
    expect(audit.action).toBe("low_confidence_override");
    expect(audit.flags).toContain("low_geocode_confidence");
    expect(audit.reviewAcknowledged).toBe(true);
    expect(audit.reviewReason).toBe("已於現場核對門牌與座標，確認為同一位置。");
  });

  it("requires review for a coarse-precision candidate even at high confidence", async () => {
    renderPanel({
      searchImpl: stubSearch({
        ok: true,
        value: searchResult([candidate({ precision: "approximate", confidence: 0.99 })]),
      }),
    });
    await runSearch();

    expect(screen.getByTestId("geocoder-candidate-requirement-0")).toHaveTextContent("需人工覆核");
  });

  it("clears the review gate state when a different candidate is picked", async () => {
    renderPanel({
      searchImpl: stubSearch({
        ok: true,
        value: searchResult([
          candidate({ candidateId: "a#0", confidence: 0.42 }),
          candidate({ candidateId: "b#1", confidence: 0.41 }),
        ]),
      }),
    });
    await runSearch();

    fireEvent.click(screen.getByTestId("geocoder-select-0"));
    fireEvent.click(screen.getByTestId("geocoder-review-ack"));
    expect(screen.getByTestId("geocoder-review-ack")).toBeChecked();

    fireEvent.click(screen.getByTestId("geocoder-select-1"));
    expect(screen.getByTestId("geocoder-review-ack")).not.toBeChecked();
  });
});

describe("errors never fabricate coordinates", () => {
  it("renders the structured error and shows no candidates", async () => {
    renderPanel({
      searchImpl: stubSearch({
        ok: false,
        error: {
          status: 502,
          code: "ODP-GEOCODER-UPSTREAM",
          summary: "google status OVER_QUERY_LIMIT",
          nextAction: "請稍後重試。",
          correlationId: "corr-err-1",
          occurredAt: "2026-08-08T10:05:00.000Z",
          retryable: true,
        },
      }),
    });
    await runSearch();

    expect(screen.getByTestId("geocoder-error")).toHaveTextContent("google status OVER_QUERY_LIMIT");
    expect(screen.getByTestId("geocoder-error-meta")).toHaveTextContent("ODP-GEOCODER-UPSTREAM");
    expect(screen.getByTestId("geocoder-error-meta")).toHaveTextContent("corr-err-1");
    expect(screen.queryByTestId("geocoder-candidate-list")).toBeNull();
  });

  it("clears a previous result when a retry fails, leaving no stale coordinates on screen", async () => {
    const searchImpl = vi
      .fn()
      .mockResolvedValueOnce({ ok: true, value: searchResult([candidate()]) })
      .mockResolvedValueOnce({
        ok: false,
        error: {
          status: 0,
          code: "ODP-GEOCODER-NETWORK",
          summary: "無法連線至地址服務。",
          nextAction: "請確認網路連線後重試。",
          correlationId: null,
          occurredAt: "2026-08-08T10:06:00.000Z",
          retryable: true,
        },
      });
    renderPanel({ searchImpl: searchImpl as never });

    await runSearch();
    expect(screen.getByTestId("geocoder-candidate-list")).toBeInTheDocument();

    await runSearch("台北市中正區信義路一段1號");
    expect(screen.queryByTestId("geocoder-candidate-list")).toBeNull();
    expect(screen.queryByTestId("geocoder-candidate-coords-0")).toBeNull();
    expect(screen.getByTestId("geocoder-error")).toBeInTheDocument();
  });

  it("does not call the provider when the endpoint is unconfigured", async () => {
    const searchImpl = stubSearch({ ok: true, value: searchResult([candidate()]) });
    renderPanel({ configuredOverride: false, searchImpl });

    expect(screen.getByTestId("geocoder-unconfigured")).toBeInTheDocument();
    await runSearch();

    expect(searchImpl).not.toHaveBeenCalled();
    expect(screen.getByTestId("geocoder-error")).toHaveTextContent("尚未設定地址定位服務");
    expect(screen.queryByTestId("geocoder-candidate-list")).toBeNull();
  });
});

describe("audited rejection and permission gating", () => {
  it("records an unresolvable address with a mandatory reason", async () => {
    const { onAudit } = renderPanel({ searchImpl: stubSearch({ ok: true, value: searchResult([]) }) });
    await runSearch();

    fireEvent.click(screen.getByTestId("geocoder-record-rejection"));
    expect(onAudit).not.toHaveBeenCalled();
    expect(screen.getByTestId("geocoder-reject-error")).toHaveTextContent("理由必填");

    fireEvent.change(screen.getByTestId("geocoder-reject-reason"), {
      target: { value: "此地址為新開發區尚未編定門牌，改以地段圖人工標記。" },
    });
    fireEvent.click(screen.getByTestId("geocoder-record-rejection"));

    expect(onAudit).toHaveBeenCalledTimes(1);
    const audit = onAudit.mock.calls[0][0];
    expect(audit.action).toBe("search_rejected");
    expect(audit.selected).toBeNull();
    expect(audit.addressRaw).toBe(QUERY);
  });

  it("shows a denial notice and no search box without the search permission", () => {
    renderPanel({ canSearch: false });

    expect(screen.getByTestId("geocoder-denied")).toBeInTheDocument();
    expect(screen.queryByTestId("geocoder-query-input")).toBeNull();
  });

  it("lets a read-only role search but not select", async () => {
    const { onSelect } = renderPanel({ canSelect: false });
    await runSearch();

    expect(screen.getByTestId("geocoder-readonly")).toBeInTheDocument();
    expect(screen.getByTestId("geocoder-select-0")).toBeDisabled();
    fireEvent.click(screen.getByTestId("geocoder-select-0"));
    expect(onSelect).not.toHaveBeenCalled();
  });
});
