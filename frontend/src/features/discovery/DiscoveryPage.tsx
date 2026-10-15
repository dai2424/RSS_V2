import { Link, useNavigate } from "react-router-dom";
import { DateRangeInputs } from "../../components/DateRangeInputs";
import { Pagination } from "../../components/Pagination";
import { showToast } from "../../components/Toast";
import {
  Badge,
  Button,
  Card,
  EmptyState,
  ErrorState,
  PageHeader,
} from "../../components/ui";
import { formatTime } from "../../lib/display";
import {
  CUSTOM_VALUE,
  RANGE_OPTIONS,
  defaultCustomRange,
  type CustomRange,
} from "../../lib/searchRange";
import { usePaginationParams } from "../../lib/usePaginationParams";
import {
  DEFAULT_DISCOVERY_RANGE,
  discoveryRangeLabel,
  discoveryWindow,
} from "../../lib/discoveryWindow";
import {
  useCreateDiscovery,
  useDeleteDiscovery,
  useDiscoveries,
  useScopeOptions,
} from "./hooks";

/**
 * 发现选题：设置范围（默认最近 7 天、全部订阅）→ 触发后台任务 → 选择候选。
 *
 * 点击只创建后台任务；列表与详情都显示服务端的真实状态（排队 / 运行 / 完成 / 失败
 * 与失败原因），不做前端假进度。筛选与分页放 URL，从详情返回能回到原来的位置。
 */
export function DiscoveryPage() {
  const navigate = useNavigate();
  const { params, setParams, size, offset, setPageOffset, setSize } =
    usePaginationParams();
  const range = params.get("range") || DEFAULT_DISCOVERY_RANGE;
  const custom: CustomRange = {
    from: params.get("from") || defaultCustomRange().from,
    to: params.get("to") || defaultCustomRange().to,
  };
  const category = params.get("category") || "";
  const sourceId = params.get("source") || "";
  const { categories, sources } = useScopeOptions();
  const batches = useDiscoveries({ limit: size, offset });
  const create = useCreateDiscovery((batchId, reused) => {
    navigate(`/discover/${batchId}`);
    showToast({
      type: "info",
      content: reused
        ? "相同范围与输入已发现过，直接打开上次的结果。"
        : "已创建发现任务，完成后会在这里列出候选。",
    });
  });
  const remove = useDeleteDiscovery();

  const filter = (key: string, value: string) => {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value);
    else next.delete(key);
    next.delete("offset");
    setParams(next, { replace: true });
  };
  const availableSources = category
    ? sources.filter((item) => item.category_id === category)
    : sources;
  const items = batches.data?.items ?? [];
  const running = create.isPending;
  const scopeText = [
    discoveryRangeLabel(range),
    categories.find((item) => item.id === category)?.name ?? "全部分类",
    availableSources.find((item) => item.id === sourceId)?.name ?? "全部来源",
  ].join(" · ");

  return (
    <div className="page page-wide">
      <PageHeader
        title="发现选题"
        description="按时间窗口读取订阅消息，由模型按「同一件事」聚合成候选事件并给出推荐理由；选定后进入写作。"
      />
      <Card className="card-pad">
        <div className="filter-bar">
          <label htmlFor="discovery-range">时间范围</label>
          <select
            id="discovery-range"
            className="input"
            value={range}
            onChange={(event) => filter("range", event.target.value)}
          >
            {RANGE_OPTIONS.filter((option) => option.value !== "all").map(
              (option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ),
            )}
            <option value={CUSTOM_VALUE}>指定日期</option>
          </select>
          <label htmlFor="discovery-category">分类</label>
          <select
            id="discovery-category"
            className="input"
            value={category}
            onChange={(event) => {
              const next = new URLSearchParams(params);
              if (event.target.value) next.set("category", event.target.value);
              else next.delete("category");
              next.delete("source");
              next.delete("offset");
              setParams(next, { replace: true });
            }}
          >
            <option value="">全部分类</option>
            {categories.map((item) => (
              <option key={item.id} value={item.id}>
                {item.name}
              </option>
            ))}
          </select>
          <label htmlFor="discovery-source">来源</label>
          <select
            id="discovery-source"
            className="input"
            value={sourceId}
            onChange={(event) => filter("source", event.target.value)}
          >
            <option value="">全部来源</option>
            {availableSources.map((item) => (
              <option key={item.id} value={item.id}>
                {item.name}
              </option>
            ))}
          </select>
          {range === CUSTOM_VALUE && (
            <DateRangeInputs
              value={custom}
              onChange={(next) => {
                const target = new URLSearchParams(params);
                target.set("from", next.from);
                target.set("to", next.to);
                target.delete("offset");
                setParams(target, { replace: true });
              }}
            />
          )}
          <Button
            disabled={running}
            onClick={() =>
              create.mutate({
                ...discoveryWindow(
                  range,
                  custom,
                  Math.floor(Date.now() / 1000),
                ),
                category_id: category || undefined,
                source_id: sourceId || undefined,
              })
            }
          >
            {running ? "正在创建…" : "发现选题"}
          </Button>
        </div>
        <p className="muted">
          当前条件：{scopeText}。窗口起止以任务创建时刻为准，结果显示在批次上。
        </p>
        {create.isError && (
          <div className="field-error" role="alert">
            {create.error.message}
          </div>
        )}
      </Card>

      {batches.isLoading && <div className="loading">正在加载发现记录…</div>}
      {batches.isError && (
        <ErrorState
          message={batches.error.message}
          onRetry={() => void batches.refetch()}
        />
      )}
      {batches.data && items.length === 0 && (
        <EmptyState
          title="还没有发现记录"
          description="选好范围后点「发现选题」；默认最近 7 天、全部订阅。"
        />
      )}
      {items.length > 0 && (
        <Card>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th scope="col">范围</th>
                  <th scope="col">消息</th>
                  <th scope="col">候选</th>
                  <th scope="col">状态</th>
                  <th scope="col">创建时间</th>
                  <th scope="col">操作</th>
                </tr>
              </thead>
              <tbody>
                {items.map((batch) => (
                  <tr key={batch.id}>
                    <td>
                      <Link className="cell-title" to={`/discover/${batch.id}`}>
                        {batch.source_name ?? batch.category_name ?? "全部订阅"}
                      </Link>
                      <div className="cell-subtitle">
                        {formatTime(batch.window_start)} 起
                      </div>
                    </td>
                    <td>{batch.message_count}</td>
                    <td>{batch.candidate_count}</td>
                    <td>
                      <BatchStatus batch={batch} />
                    </td>
                    <td>{formatTime(batch.created_at)}</td>
                    <td>
                      <div className="toolbar">
                        <Link
                          className="button secondary"
                          to={`/discover/${batch.id}`}
                        >
                          打开
                        </Link>
                        <Button
                          className="danger"
                          disabled={remove.isPending}
                          onClick={() =>
                            remove.mutate(batch.id, {
                              onSuccess: () =>
                                showToast({
                                  type: "success",
                                  content: "已删除这次发现。",
                                }),
                              onError: (error) =>
                                showToast({
                                  type: "error",
                                  content: error.message,
                                }),
                            })
                          }
                        >
                          删除
                        </Button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <Pagination
            total={batches.data?.total ?? 0}
            offset={offset}
            size={size}
            unit="批"
            disabled={batches.isPlaceholderData}
            onPage={setPageOffset}
            onSize={(next) => setSize(next, batches.data?.total ?? 0)}
          />
        </Card>
      )}
    </div>
  );
}

/** 批次状态：任务在排队/运行时以任务为准，否则用批次的执行结果。 */
function BatchStatus({
  batch,
}: {
  batch: {
    status: string;
    task_status: string | null;
    error_message: string | null;
  };
}) {
  const status = batch.task_status === "queued" ? "queued" : batch.status;
  const tone =
    status === "succeeded"
      ? "success"
      : status === "failed"
        ? "danger"
        : "warning";
  const label =
    status === "queued"
      ? "已排队"
      : status === "running"
        ? "运行中"
        : status === "succeeded"
          ? "已完成"
          : "失败";
  return (
    <>
      <Badge tone={tone}>{label}</Badge>
      {status === "failed" && batch.error_message && (
        <div className="cell-subtitle">{batch.error_message}</div>
      )}
    </>
  );
}
