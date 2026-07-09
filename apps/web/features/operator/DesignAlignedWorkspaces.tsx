"use client";

import type { CSSProperties } from "react";
import { ISSUE_FIXTURES } from "./fixtures";
import styles from "./designAligned.module.css";
import type { Issue } from "./types";
import type { StoreOpsWorkflowDialogType } from "./storeOpsWorkflowTypes";

type DesignTodayWorkspaceProps = {
  onQueueSelect: (workspaceId: "store" | "growth" | "network" | "govern") => void;
};

type DesignStoreOpsWorkspaceProps = {
  onOpenWorkflow: (dialog: StoreOpsWorkflowDialogType, issue: Issue) => void;
};

const kpis = [
  { label: "高風險未指派", value: "1", note: "下一步：完成 Triage 與指派", tone: "danger" },
  { label: "已逾期 Issue", value: "1", note: "優先處理 SLA 逾期", tone: "danger" },
  { label: "即將逾期", value: "4", note: "今日期限內需完成", tone: "warn" },
  { label: "成效待判斷", value: "1", note: "請完成 Outcome Review", tone: "info" },
  { label: "待我核准", value: "4", note: "前往治理稽核・核准中心", tone: "info" },
  { label: "需升級門市", value: "1", note: "連續紅燈・店網重估", tone: "muted" },
];

const todayRows = [
  {
    id: "ISS-1021",
    title: "Kiosk 離線＋遠端重啟失敗",
    store: "皇羽自助洗衣 新莊店",
    signals: ["設備異常", "IoT", "支付"],
    state: "已指派",
    due: "已逾期 1h 24m",
    owner: "陳建宏",
    cta: "建立處置",
    tone: "danger",
  },
  {
    id: "ISS-1024",
    title: "付款機前卡住＋付款失敗＋Google 負評",
    store: "Oday 信義松仁店",
    signals: ["支付異常", "評價", "客服", "影像", "支付", "IoT"],
    state: "新進",
    due: "3h 12m",
    owner: "未指派",
    cta: "完成 Triage",
    tone: "danger",
  },
  {
    id: "ISS-1015",
    title: "地面髒亂 Camera 事件＋Google 一星評論",
    store: "Oday 大安和平店",
    signals: ["清潔品質", "影像", "評價", "清潔"],
    state: "已分類",
    due: "2h 40m",
    owner: "未指派",
    cta: "指派 Owner",
    tone: "warn",
  },
  {
    id: "ISS-1019",
    title: "烘不乾客訴增加＋乾衣機 cycle 異常",
    store: "Oday 板橋府中店",
    signals: ["設備異常", "客服", "IoT"],
    state: "處置中",
    due: "明日 18:00 前",
    owner: "陳建宏",
    cta: "提交現場回報",
    tone: "warn",
  },
  {
    id: "ISS-1008",
    title: "離峰閒置率高＋會員回訪下降",
    store: "洗多星 中壢中原店",
    signals: ["營收／需求", "預測", "支付"],
    state: "成效待判斷",
    due: "今日內判斷",
    owner: "黃仕杰",
    cta: "判斷成效",
    tone: "muted",
  },
  {
    id: "ISS-1017",
    title: "退款申請 NT$180 逾 24h 未處理",
    store: "Oday 板橋府中店",
    signals: ["支付／退款", "客服", "支付"],
    state: "已指派",
    due: "今日 16:00 前",
    owner: "張珮珊",
    cta: "建立處置",
    tone: "muted",
  },
];

const decisions = [
  { tag: "核准", time: "7/8 前", title: "SiteScore 審核：板橋府中候選點（WAIT 76）" },
  { tag: "核准", time: "7/4 18:00 前", title: "活動核准：60 天未回訪會員召回（LINE 推播）" },
  { tag: "核准", time: "今日 17:00 前", title: "退款批次核准：7 筆／NT$1,240" },
  { tag: "成效判斷", time: "今日內", title: "ISS-1008：離峰閒置率高＋會員回訪下降" },
];

const riskStores = [
  { name: "Oday 信義松仁店", note: "支付異常處理中（ISS-1024）", tone: "warn" },
  { name: "Oday 板橋府中店", note: "連續 8 週橙／紅燈・營收下滑", tone: "warn" },
  { name: "Oday 大安和平店", note: "租金壓力高・回本期延長", tone: "warn" },
  { name: "洗多星 中壢中原店", note: "連續 90 天紅燈・重配候選", tone: "danger" },
  { name: "皇羽自助洗衣 新莊店", note: "低回訪＋商圈變化・Kiosk 工單處理中", tone: "warn" },
];

const storeQueue = [
  {
    id: "ISS-1021",
    title: "Kiosk 離線＋遠端重啟失敗",
    store: "皇羽自助洗衣 新莊店",
    status: "已指派",
    due: "已逾期 1h 24m",
    tags: ["IoT", "支付"],
    owner: "陳建宏",
    tone: "danger",
    next: "建立處置",
  },
  {
    id: "ISS-1024",
    title: "付款機前卡住＋付款失敗＋Google 負評",
    store: "Oday 信義松仁店",
    status: "新進",
    due: "3h 12m",
    tags: ["評價", "客服", "影像", "支付", "IoT"],
    owner: "未指派",
    tone: "danger",
    next: "完成 Triage",
  },
  {
    id: "ISS-1015",
    title: "地面髒亂 Camera 事件＋Google 一星評論",
    store: "Oday 大安和平店",
    status: "警示",
    due: "2h 40m",
    tags: ["影像", "評價", "清潔"],
    owner: "未指派",
    tone: "warn",
    next: "指派 Owner",
  },
  {
    id: "ISS-1019",
    title: "烘不乾客訴增加＋乾衣機 cycle 異常",
    store: "Oday 板橋府中店",
    status: "警示",
    due: "明日 18:00 前",
    tags: ["客服", "IoT"],
    owner: "陳建宏",
    tone: "warn",
    next: "提交現場回報",
  },
  {
    id: "ISS-1008",
    title: "離峰閒置率高＋會員回訪下降",
    store: "洗多星 中壢中原店",
    status: "一般",
    due: "今日內判斷",
    tags: ["預測", "支付"],
    owner: "黃仕杰",
    tone: "muted",
    next: "判斷成效",
  },
];

export function DesignTodayWorkspace({ onQueueSelect }: DesignTodayWorkspaceProps) {
  return (
    <div className={styles.todayWorkspace} data-screen-label="Today 今日工作">
      <header className={styles.hero}>
        <div>
          <h1>早安，林承翰 — 營運主管</h1>
          <p>資料範圍：全品牌・12 門市・北北桃</p>
        </div>
        <div className={styles.heroMeta}>
          <span>2026/07/05 ・週日</span>
          <strong>Demo 視角：營運主管</strong>
        </div>
      </header>

      <section className={styles.kpiGrid} aria-label="Today KPI cards">
        {kpis.map((item) => (
          <article className={styles.kpiCard} data-tone={item.tone} key={item.label}>
            <span>{item.label}</span>
            <strong>{item.value}</strong>
            <p>{item.note}</p>
          </article>
        ))}
      </section>

      <div className={styles.todayGrid}>
        <section className={styles.tablePanel} aria-label="今天最需要處理">
          <div className={styles.panelHeader}>
            <div>
              <h2>今天最需要處理</h2>
              <span>依嚴重度與 SLA 排序</span>
            </div>
            <span>6 項</span>
          </div>
          <div className={styles.issueTable}>
            {todayRows.map((row) => (
              <button className={styles.todayRow} key={row.id} onClick={() => onQueueSelect("store")} type="button">
                <i data-tone={row.tone} />
                <span className={styles.rowMain}>
                  <small>{row.id}</small>
                  <strong>{row.title}</strong>
                  <span>{row.store}</span>
                  <span className={styles.tagLine}>
                    {row.signals.map((signal) => (
                      <b key={signal}>{signal}</b>
                    ))}
                  </span>
                </span>
                <span className={styles.rowState}>
                  <b>{row.state}</b>
                  <em>{row.due}</em>
                </span>
                <span className={styles.rowOwner}>
                  <small>Owner</small>
                  <strong>{row.owner}</strong>
                </span>
                <span className={styles.rowCta}>{row.cta} →</span>
              </button>
            ))}
          </div>
        </section>

        <aside className={styles.todayRail}>
          <section className={styles.railPanel}>
            <div className={styles.panelHeader}>
              <h2>需要你決策</h2>
            </div>
            <div className={styles.decisionStack}>
              {decisions.map((item) => (
                <button className={styles.decisionItem} key={item.title} onClick={() => onQueueSelect("govern")} type="button">
                  <span>
                    <b>{item.tag}</b>
                    {item.time}
                  </span>
                  <strong>{item.title}</strong>
                  <em>進行核准 →</em>
                </button>
              ))}
            </div>
          </section>

          <section className={styles.railPanel}>
            <div className={styles.panelHeader}>
              <h2>門市風險快照</h2>
              <span>12 門市・示意</span>
            </div>
            <div className={styles.riskMap} aria-label="門市風險地圖示意">
              {riskStores.map((store, index) => (
                <i data-tone={store.tone} key={store.name} style={{ "--x": `${18 + index * 16}%`, "--y": `${34 + (index % 3) * 14}%` } as CSSProperties} />
              ))}
            </div>
            <div className={styles.riskList}>
              {riskStores.map((store) => (
                <div key={store.name}>
                  <i data-tone={store.tone} />
                  <strong>{store.name}</strong>
                  <span>{store.note}</span>
                </div>
              ))}
            </div>
          </section>

          <section className={styles.railPanel}>
            <div className={styles.panelHeader}>
              <h2>最近動態</h2>
              <span>AUDIT FEED</span>
            </div>
            <div className={styles.auditMini}>
              <p><time>09:12</time> 系統 ForecastOps 捕捉連續紅燈（90 天）</p>
              <p><time>08:44</time> 支付異常自動併入 ISS-1024</p>
              <p><time>08:20</time> 核准中心新增 SiteScore WAIT 76</p>
            </div>
          </section>
        </aside>
      </div>
    </div>
  );
}

export function DesignStoreOpsWorkspace({ onOpenWorkflow }: DesignStoreOpsWorkspaceProps) {
  const issue = ISSUE_FIXTURES.find((item) => item.id === "ISS-1024") ?? ISSUE_FIXTURES[0];

  return (
    <div className={styles.storeWorkspace} data-screen-label="Store Ops 門市營運">
      <header className={styles.storeHeader}>
        <h1>門市營運</h1>
        <p>問題 → 證據 → 指派 → 處置 → 觀察 → 成效，在同一個工作台完成</p>
      </header>

      <div className={styles.storeGrid}>
        <aside className={styles.storeQueue} aria-label="門市 Issue queue">
          <label className={styles.designSearch}>
            <input placeholder="搜尋標題／門市／編號" />
          </label>
          <div className={styles.filterRows}>
            <span>全部 7</span>
            <span>待處理 4</span>
            <span>處置中 1</span>
            <span>觀察／成效 1</span>
          </div>
          <div className={styles.filterRows}>
            <span>全部</span>
            <span>客服類</span>
            <span>設備類</span>
            <span>評價</span>
            <span>影像</span>
            <span>支付</span>
            <span>預測</span>
            <span>只看我的</span>
          </div>
          <div className={styles.storeQueueList}>
            {storeQueue.map((row) => (
              <button className={styles.storeQueueItem} data-active={row.id === "ISS-1024"} key={row.id} type="button">
                <span className={styles.storeTopline}>
                  <small>{row.id}</small>
                  <b data-tone={row.tone}>{row.status}</b>
                  <em>{row.due}</em>
                </span>
                <strong>{row.title}</strong>
                <span>{row.store}・設備異常</span>
                <span className={styles.tagLine}>
                  {row.tags.map((tag) => (
                    <b key={tag}>{tag}</b>
                  ))}
                </span>
                <span className={styles.nextLine}>下一步：{row.next}</span>
              </button>
            ))}
          </div>
        </aside>

        <main className={styles.storeDetail} aria-label="ISS-1024 detail">
          <section className={styles.issueHero}>
            <div>
              <span className={styles.issueId}>ISS-1024</span>
              <b>嚴重</b>
              <b>新進</b>
            </div>
            <h2>付款機前卡住＋付款失敗＋Google 負評</h2>
            <p>
              門市付款機自 07:58 起出現交易逾時尖峰，Camera 偵測顧客於 kiosk 前停留異常，
              同時段新增 1 則付款相關一星評價與 3 件 AI 未解決客服案件。
            </p>
            <ol className={styles.progress}>
              {["新進", "分類", "指派", "處置", "執行", "觀察", "成效", "結案"].map((step, index) => (
                <li data-active={index === 0} key={step}>{step}</li>
              ))}
            </ol>
            <div className={styles.nextStep}>下一步　請完成 Triage — 判斷根因分類與信心度</div>
          </section>

          <section className={styles.storeStrip}>
            <span><small>門市</small><strong>Oday 信義松仁店</strong></span>
            <span><small>型態</small><strong>自助洗衣</strong></span>
            <span><small>機台</small><strong>14 台</strong></span>
            <span><small>今日營收</small><strong>NT$18,420</strong></span>
            <span><small>FORECASTOPS 四燈</small><strong>需求・設備・清潔・客訴</strong></span>
          </section>

          <section className={styles.evidenceFusion}>
            <div className={styles.sectionTitle}>
              <h3>證據融合</h3>
              <span>EVIDENCE FUSION</span>
              <strong>證據強度　強</strong>
            </div>
            <div className={styles.evidenceCards}>
              <article><small>Google 評價</small><strong>1.0 ★ 未回覆</strong><p>付款失敗／找不到客服 今日 08:12</p></article>
              <article><small>客服案件</small><strong>3 件（AI 未解決為主）</strong><p>intent: payment_failed 持續更新</p></article>
              <article><small>Camera 事件</small><strong>customer_stuck_at_kiosk</strong><p>信心 86%・場域事件</p></article>
              <article><small>支付</small><strong>失敗率 12.4%</strong><p>較 baseline +8.1pp 即時</p></article>
              <article><small>IoT 設備</small><strong>交易逾時尖峰</strong><p>30 分內 14 次</p></article>
              <article><small>ForecastOps</small><strong>四燈評估</strong><p>設備紅燈為主因</p></article>
            </div>

            <div className={styles.metricChart}>
              <div>
                <small>付款失敗率</small>
                <strong>12.4%</strong>
                <span>+8.1pp vs baseline</span>
              </div>
              <div className={styles.bars}>
                {Array.from({ length: 14 }, (_, index) => (
                  <i key={index} style={{ height: `${18 + index * 4}px` }} />
                ))}
              </div>
            </div>

            <div className={styles.evidenceLists}>
              <section>
                <h4>支持證據 4</h4>
                <p>付款失敗率 12.4%，較同時段 baseline +8.1pp</p>
                <p>Camera 事件 customer_stuck_at_kiosk（信心 86%）</p>
                <p>3 件 AI 未解決案件 intent 均為 payment_failed</p>
              </section>
              <section>
                <h4>反向證據 1</h4>
                <p>店內人流正常，可排除需求面異常</p>
              </section>
            </div>

            <div className={styles.aiBox}>
              <b>AI 建議</b>
              指派工務檢查付款機（讀卡模組／韌體），客服先回覆 Google 負評並建立退款處理；處置後觀察 14 天付款失敗率與負評主題是否下降。
            </div>
          </section>
        </main>

        <aside className={styles.actionRail} aria-label="Action rail">
          <section>
            <h2>ACTION RAIL <span>下一步</span></h2>
            <dl>
              <div><dt>狀態</dt><dd>新進</dd></div>
              <div><dt>Owner</dt><dd>未指派</dd></div>
              <div><dt>SLA</dt><dd>3h 12m</dd></div>
              <div><dt>期限</dt><dd>今日 13:00 前完成 Triage</dd></div>
            </dl>
            <button className={styles.primaryAction} onClick={() => onOpenWorkflow("triage", issue)} type="button">
              完成 Triage
            </button>
            <button className={styles.secondaryAction} onClick={() => onOpenWorkflow("escalate", issue)} type="button">
              升級（Growth／Network／Govern）
            </button>
          </section>

          <section>
            <h2>AUDIT TIMELINE <span>全部 →</span></h2>
            <div className={styles.auditMini}>
              <p><time>08:20</time> 系統 3 件 AI 未解決客服案件併入 Issue</p>
              <p><time>08:12</time> 系統 新增 Google 一星評價 → 併入 Issue</p>
              <p><time>08:05</time> 系統 Camera 事件 customer_stuck_at_kiosk</p>
              <p><time>07:58</time> 系統 付款失敗率超過閾值 → 建立 Issue</p>
            </div>
          </section>
        </aside>
      </div>
    </div>
  );
}
