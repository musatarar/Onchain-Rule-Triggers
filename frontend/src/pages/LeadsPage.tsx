import { useEffect, useState } from 'react';
import { errorMessage } from '../api/client';
import { fetchLeads } from '../api/endpoints';
import type { LeadRecord } from '../api/types';
import { EmptyState, ErrorMessage } from '../components/Messages';
import { PageHeader } from '../components/PageHeader';
import { LeadsTable } from '../components/leads/LeadsTable';
import { DEFAULT_SORT, sortLeads } from '../components/leads/leadTable';
import type { SortKey, SortState } from '../components/leads/leadTable';
import '../components/leads/leads.css';

/**
 * The book of leads — where signing in lands you.
 */
export function LeadsPage() {
  const [leads, setLeads] = useState<LeadRecord[]>([]);
  const [sort, setSort] = useState<SortState>(DEFAULT_SORT);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let active = true;

    fetchLeads()
      .then((records) => {
        if (active) setLeads(records);
      })
      .catch((err: unknown) => {
        if (active) setError(`Failed to load leads: ${errorMessage(err)}`);
      })
      .finally(() => {
        if (active) setLoading(false);
      });

    return () => {
      active = false;
    };
  }, []);

  /** Clicking the sorted column reverses it; any other column starts ascending. */
  function handleSort(key: SortKey) {
    setSort((current) =>
      current.key === key
        ? { key, direction: current.direction === 'asc' ? 'desc' : 'asc' }
        : { key, direction: 'asc' },
    );
  }

  const ordered = sortLeads(leads, sort.key, sort.direction);

  return (
    <>
      <PageHeader
        current="/leads/"
        title="Leads"
        subtitle="The whole book, stalest contact first"
      />

      <div className="container">
        {error && <ErrorMessage>{error}</ErrorMessage>}

        {loading ? (
          <EmptyState>Loading…</EmptyState>
        ) : ordered.length === 0 ? (
          <EmptyState>No leads yet.</EmptyState>
        ) : (
          <>
            <p className="leads-count">{ordered.length} leads</p>
            <LeadsTable leads={ordered} sort={sort} onSort={handleSort} />
          </>
        )}
      </div>
    </>
  );
}
