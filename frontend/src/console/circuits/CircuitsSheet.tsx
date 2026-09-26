import { useState } from 'react';
import { Link } from 'react-router-dom';
import { api, errorMessage } from '../api/index.ts';
import type { Rule } from '../api/types.ts';
import { Circuit } from '../circuit/Circuit.tsx';
import { Glyph, Ident } from '../circuit/Glyph.tsx';
import { plural } from '../derive/format.ts';
import { useConsole } from '../state.tsx';
import { Icon } from '../ui/Icon.tsx';
import { EmptyState, ErrorState, Skeleton } from '../ui/States.tsx';

function CircuitRow({ rule }: { rule: Rule }) {
  const { describer, refresh, showToast } = useConsole();
  const [busy, setBusy] = useState(false);
  const { match_count: count, unevaluable_count: unevaluable } = rule.stats;

  const toggle = () => {
    setBusy(true);
    api
      .updateRule(rule.id, { enabled: !rule.enabled })
      .then(
        (saved) => {
          refresh();
          showToast(`${saved.tag} ${saved.enabled ? 'armed' : 'switched off'}`);
        },
        (error: unknown) => showToast(`Couldn't switch ${rule.tag}: ${errorMessage(error)}`),
      )
      .finally(() => setBusy(false));
  };

  return (
    <div className={`rung-row${rule.enabled ? '' : ' off'}`}>
      <div className="no">
        <Glyph name={rule.glyph} className="xl" />
      </div>
      <div className="body">
        <h3>
          <Ident rule={rule} big /> <span>{rule.name}</span>
          {unevaluable > 0 && (
            <span className="tag abn" title="Transfers of tokens whose decimals are unknown">
              <Icon name="warn" />
              {unevaluable} unevaluable
            </span>
          )}
        </h3>
        {rule.sentence && <p className="sentence">“{rule.sentence}”</p>}
        <Circuit condition={rule.condition} tag={rule.tag} glyph={rule.glyph} describer={describer} label={`Circuit ${rule.tag}`} />
      </div>
      <div className="side">
        {rule.enabled ? (
          <Link className="xref" to={`/journal/?channel=${rule.id}`}>
            <b>{count}</b>
            <span>{plural(count, 'match', 'matches')} in journal</span>
          </Link>
        ) : (
          <div className="xref">
            <b>—</b>
            <span className="plain">Off, not tested</span>
          </div>
        )}
        <button
          type="button"
          className="switch"
          role="switch"
          aria-checked={rule.enabled}
          aria-label={`${rule.tag} armed`}
          aria-busy={busy}
          disabled={busy}
          onClick={toggle}
        >
          <span className="trk" />
          {rule.enabled ? 'Armed' : 'Off'}
        </button>
        <Link className="btn sm" to={`/circuits/${rule.id}/`}>
          <Icon name="edit" />
          Edit
        </Link>
      </div>
    </div>
  );
}

export function CircuitsSheet() {
  const { rules } = useConsole();
  let body;
  if (rules.data) {
    body = rules.data.length ? (
      rules.data.map((rule) => <CircuitRow key={rule.id} rule={rule} />)
    ) : (
      <EmptyState title="No circuits yet.">
        Describe the transactions to catch with <Link to="/circuits/new/">NEW CIRCUIT</Link>.
      </EmptyState>
    );
  } else if (rules.status === 'error') {
    body = <ErrorState what="Couldn't load the circuits." error={rules.error} onRetry={rules.reload} />;
  } else {
    body = <Skeleton rows={3} lines={4} label="Loading circuits" />;
  }
  return (
    <section className="pane sheet" aria-label="Circuits">
      <div className="pane-h">
        <h1>Circuits</h1>
        <span className="lbl">Every armed circuit is tested against every transaction. Circuits are not first-match.</span>
      </div>
      <div className="scroll program">{body}</div>
    </section>
  );
}
