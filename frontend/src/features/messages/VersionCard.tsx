import type { components } from "../../api/generated";
import { Badge, Card } from "../../components/ui";
import {
  Tabs,
  TabsContent,
  TabsList,
  TabsTrigger,
} from "../../components/ui/tabs";
import { formatTime, safeLink, statusLabels } from "../../lib/display";

type Version = components["schemas"]["MessageVersionResponse"];
type Generated = NonNullable<Version["enrichments"]>[number];

/** 任务仍在排队或运行时，失败只是上一次尝试，避免与「任务已排队」看起来矛盾。 */
function isRetrying(status?: string): boolean {
  return status === "queued" || status === "running";
}

/** 机器生成结果的状态说明：成功给正文，其余显示状态和失败原因。 */
function GeneratedAlert({
  kind,
  message,
  error,
  retrying,
}: {
  kind: string;
  message: string;
  error?: string | null;
  retrying?: boolean;
}) {
  const label = retrying
    ? `上次尝试：${statusLabels[message]}，任务已排队自动重试`
    : `${kind}状态：${statusLabels[message]}`;
  return (
    <div className="alert">
      {label}
      {error ? `：${error}` : ""}
    </div>
  );
}

function EnrichmentPanel({
  enrichment,
  retrying,
}: {
  enrichment?: Generated;
  retrying?: boolean;
}) {
  if (enrichment?.status !== "succeeded") {
    return enrichment ? (
      <GeneratedAlert
        kind="加工"
        message={enrichment.status}
        error={enrichment.error_message}
        retrying={retrying}
      />
    ) : (
      <div className="alert">
        此版本尚无内容摘要。超过长度阈值的消息会在采集后自动加工，也可以在顶部手动生成。
      </div>
    );
  }
  return (
    <article className="prose">
      <h2>{enrichment.title}</h2>
      <h3>摘要</h3>
      <p>{enrichment.summary}</p>
      <h3>关键词</h3>
      <div className="badge-row">
        {enrichment.keywords.length === 0 && (
          <span className="muted">无关键词</span>
        )}
        {enrichment.keywords.map((keyword) => (
          <Badge key={keyword} tone="neutral">
            {keyword}
          </Badge>
        ))}
      </div>
      <p className="muted">
        模型：{enrichment.model} · 机器生成，用于浏览和检索，请核对原文。
      </p>
      <details className="muted">
        <summary>加工记录</summary>
        <dl className="metadata-list">
          <dt>提示词版本</dt>
          <dd>{enrichment.prompt_version}</dd>
          <dt>Provider</dt>
          <dd>{enrichment.provider_id}</dd>
          <dt>任务 ID</dt>
          <dd>{enrichment.task_id}</dd>
        </dl>
      </details>
    </article>
  );
}

export function VersionCard({ version }: { version: Version }) {
  const translation =
    version.translations.find((v) => v.status === "succeeded") ??
    version.translations[0];
  const enrichment =
    version.enrichments.find((v) => v.status === "succeeded") ??
    version.enrichments[0];
  const href = safeLink(version.url);
  return (
    <Card className="version-card">
      <div className="version-meta">
        <Badge>v{version.version_number}</Badge>
        <Badge>{statusLabels[version.language]}</Badge>
        <span className="muted">
          发布：{formatTime(version.published_at)} · 采集：
          {formatTime(version.collected_at)}
        </span>
        {href && (
          <a className="muted" href={href} target="_blank" rel="noreferrer">
            原文链接 ↗
          </a>
        )}
      </div>
      <Tabs defaultValue="original">
        <TabsList>
          <TabsTrigger value="original">原文</TabsTrigger>
          <TabsTrigger value="chinese">中文版本（机器生成）</TabsTrigger>
          <TabsTrigger value="enrichment">内容摘要（机器生成）</TabsTrigger>
        </TabsList>
        <TabsContent value="original">
          <article className="prose">
            <h2>{version.title}</h2>
            {version.summary && (
              <>
                <h3>摘要</h3>
                <p>{version.summary}</p>
              </>
            )}
            {version.content && (
              <>
                <h3>正文</h3>
                <p>{version.content}</p>
              </>
            )}
            {!version.summary && !version.content && (
              <p>该版本没有摘要或正文。</p>
            )}
            {version.summary && !version.content && (
              <p className="muted">
                该来源未提供独立正文，以上为来源给出的描述。
              </p>
            )}
          </article>
        </TabsContent>
        <TabsContent value="chinese">
          {translation?.status === "succeeded" ? (
            <article className="prose">
              <h2>{translation.title}</h2>
              <h3>摘要</h3>
              <p>{translation.summary}</p>
              <h3>正文</h3>
              <p>{translation.content}</p>
              <p className="muted">
                模型：{translation.model} · 机器翻译，请核对原文。
              </p>
              <details className="muted">
                <summary>翻译记录</summary>
                <dl className="metadata-list">
                  <dt>提示词版本</dt>
                  <dd>{translation.prompt_version}</dd>
                  <dt>Provider</dt>
                  <dd>{translation.provider_id}</dd>
                  <dt>任务 ID</dt>
                  <dd>{translation.task_id}</dd>
                </dl>
              </details>
            </article>
          ) : (
            <GeneratedAlert
              kind="翻译"
              message={translation?.status ?? "pending"}
              error={translation?.error_message}
              retrying={isRetrying(version.translation_task?.status)}
            />
          )}
        </TabsContent>
        <TabsContent value="enrichment">
          <EnrichmentPanel
            enrichment={enrichment}
            retrying={isRetrying(version.enrichment_task?.status)}
          />
        </TabsContent>
      </Tabs>
    </Card>
  );
}
