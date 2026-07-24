import type { Job } from "@/api/jobs";
import { EmptyState } from "@/components/common/empty-state";
import { Panel, PanelContent, PanelHeader } from "@/components/common/panel";
import { isActive } from "@/lib/job-status";
import { DownloadIcon, InboxIcon } from "lucide-react";
import { useTranslation } from "react-i18next";
import { JobCard } from "./job-card";

type Props = {
  jobs: Job[];
  isLoading: boolean;
  onCancel: (jobId: string) => void;
  onDelete: (jobId: string) => void;
};

export function JobsPanel({ jobs, isLoading, onCancel, onDelete }: Props) {
  const { t } = useTranslation();

  return (
    <Panel>
      <PanelHeader
        leadingIcon={<DownloadIcon size={18} />}
        badge={
          jobs.length > 0 && (
            <span className="text-foreground-400 font-mono text-xs">
              ({jobs.length})
            </span>
          )
        }
      >
        {t("downloads.title")}
      </PanelHeader>
      <PanelContent height="h-124" className="space-y-2">
        {isLoading ? (
          <div className="flex h-full items-center justify-center">
            <span className="text-foreground-400 text-small font-mono">
              {t("common.loading")}
            </span>
          </div>
        ) : jobs.length === 0 ? (
          <EmptyState icon={InboxIcon} title={t("downloads.empty")} />
        ) : (
          <div className="flex flex-col gap-2">
            {jobs.map((job) => (
              <JobCard
                key={job.id}
                job={job}
                onCancel={isActive(job.status) ? onCancel : undefined}
                onDelete={!isActive(job.status) ? onDelete : undefined}
              />
            ))}
          </div>
        )}
      </PanelContent>
    </Panel>
  );
}
