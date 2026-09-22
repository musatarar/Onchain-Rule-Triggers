import type { LeadRecord } from '../../api/types';
import { formatDateOnly, formatStage, formatUsdCompact } from '../../util/labels';
import type { SortDirection, SortKey, SortState } from './leadTable';

interface Column {
  key: SortKey;
  label: string;
  /** Right-aligned, mono: figures read as machine output. */
  numeric?: boolean;
}

const COLUMNS: Column[] = [
  { key: 'agency_name', label: 'Agency' },
  { key: 'contact_name', label: 'Contact' },
  { key: 'stage', label: 'Stage' },
  { key: 'estimated_book_size_usd', label: 'Book size', numeric: true },
  { key: 'last_contacted_date', label: 'Last contacted', numeric: true },
];

/** `aria-sort` carries a direction only on the column actually sorted. */
function ariaSort(active: boolean, direction: SortDirection) {
  if (!active) return 'none' as const;
  return direction === 'asc' ? ('ascending' as const) : ('descending' as const);
}

interface Props {
  leads: LeadRecord[];
  sort: SortState;
  onSort: (key: SortKey) => void;
}

/**
 * The book, as a table. Presentational only: ordering arrives already
 * resolved, so this file holds no logic worth testing and the logic that
 * matters is tested without a DOM.
 */
export function LeadsTable({ leads, sort, onSort }: Props) {
  return (
    <div className="leads-table-wrap">
      <table className="leads-table">
        <caption className="leads-table__caption">
          Leads, sorted by {sort.key.replace(/_/g, ' ')}, {sort.direction}ending.
        </caption>
        <thead>
          <tr>
            {COLUMNS.map((column) => {
              const active = sort.key === column.key;
              return (
                <th
                  key={column.key}
                  scope="col"
                  aria-sort={ariaSort(active, sort.direction)}
                  className={column.numeric ? 'leads-table__num' : undefined}
                >
                  <button
                    type="button"
                    className="leads-table__sort"
                    onClick={() => onSort(column.key)}
                  >
                    {column.label}
                    {/* Only the sorted column carries an arrow: an indicator on
                        every header reads as five sorted columns. */}
                    <span aria-hidden="true" className="leads-table__arrow">
                      {active ? (sort.direction === 'asc' ? '↑' : '↓') : ''}
                    </span>
                  </button>
                </th>
              );
            })}
          </tr>
        </thead>
        <tbody>
          {leads.map((lead) => (
            <tr key={lead.id}>
              <td>
                <span className="leads-table__agency">{lead.data.agency_name ?? lead.id}</span>
                <span className="leads-table__id">{lead.id}</span>
              </td>
              <td>
                <span className="leads-table__contact">{lead.data.contact_name ?? '—'}</span>
                {lead.data.contact_email ? (
                  <a className="leads-table__email" href={`mailto:${lead.data.contact_email}`}>
                    {lead.data.contact_email}
                  </a>
                ) : (
                  <span className="leads-table__email">—</span>
                )}
              </td>
              <td>{formatStage(lead.data.stage ?? '')}</td>
              <td className="leads-table__num">
                {lead.data.estimated_book_size_usd === undefined
                  ? '—'
                  : formatUsdCompact(lead.data.estimated_book_size_usd)}
              </td>
              <td className="leads-table__num">
                {formatDateOnly(lead.data.last_contacted_date ?? null)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
