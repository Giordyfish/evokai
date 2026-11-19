/** The status of an eval, red team or model comparison run, as a tag. */
import { t } from '../i18n'
import { Tag } from '../ui'

export type RunStatus = 'running' | 'done' | 'limit' | 'failed' | 'interrupted'

export const statusTag = (s: RunStatus) =>
  s === 'done' ? (
    <Tag tone="ok" dot>
      {t('evals.st.done')}
    </Tag>
  ) : s === 'running' ? (
    <Tag tone="vi" dot>
      {t('evals.st.running')}
    </Tag>
  ) : s === 'limit' ? (
    <Tag tone="warn" dot>
      {t('evals.st.limit')}
    </Tag>
  ) : (
    <Tag tone="bad" dot>
      {t(s === 'failed' ? 'evals.st.failed' : 'evals.st.interrupted')}
    </Tag>
  )
