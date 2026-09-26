import type { CSSProperties } from 'react';
import type { GateTrace, MatchDetail } from '../api/types.ts';
import { CircuitSvg, useBoxWidth, usePlayback, useScene } from '../circuit/Circuit.tsx';
import { Ident } from '../circuit/Glyph.tsx';
import { type Describer, gateSentence } from '../derive/describe.ts';
import { explain, gateTag, observedText, powerText, wiringText } from '../derive/explain.ts';
import { formatUnits, groupDigits, plural, short, utcDateTime } from '../derive/format.ts';
import type { Scene } from '../derive/layout.ts';
import { Icon } from '../ui/Icon.tsx';

export type Inspect = number | 'coil';

const EXPLORERS: Record<number, string> = { 1: 'https://etherscan.io' };

function Address({ chain, address, label }: { chain: number; address: string | null; label: string | null }) {
  if (!address) return <span className="mono">contract creation</span>;
  const explorer = EXPLORERS[chain];
  return (
    <>
      {explorer ? (
        <a className="mono" href={`${explorer}/address/${address}`} target="_blank" rel="noopener noreferrer">
          {address}
        </a>
      ) : (
        <span className="mono">{address}</span>
      )}
      {label && <span className="tag">{label}</span>}
    </>
  );
}

/** Every address label the match carries, from the headline and from what gates read. */
function labelsOf(detail: MatchDetail): Map<string, string> {
  const labels = new Map<string, string>();
  const { headline } = detail;
  if (headline.from_label) labels.set(headline.from_address, headline.from_label);
  if (headline.to_label && headline.to_address) labels.set(headline.to_address, headline.to_label);
  for (const gate of Object.values(detail.trace ?? {})) {
    const seen = gate.observed;
    if (seen?.kind === 'address' && seen.address && seen.label) labels.set(seen.address, seen.label);
  }
  return labels;
}

const TAG_CLASS = { 'CARRIED POWER': 'tag pw', 'NO DATA': 'tag abn', BLOCKED: 'tag' } as const;

function Inspector({
  detail,
  trace,
  scene,
  inspect,
  pinned,
  playing,
  describer,
  onInspect,
  onUnpin,
}: {
  detail: MatchDetail;
  /** The match's trace: only a match with one has a circuit to inspect. */
  trace: Record<number, GateTrace>;
  scene: Scene;
  inspect: Inspect;
  pinned: boolean;
  playing: boolean;
  describer: Describer;
  onInspect: (pick: Inspect) => void;
  onUnpin: () => void;
}) {
  const unpin = (
    <button type="button" className="unpin" onClick={onUnpin} aria-label="Close the inspector">
      <Icon name="x" />
    </button>
  );
  const gates = scene.gates;
  const gate = inspect === 'coil' ? undefined : gates.find((g) => g.node.id === inspect);
  if (gate) {
    const power = scene.power.gates.find((g) => g.node === gate.node)!;
    const insight = explain(gate.node, trace[inspect as number], detail, describer);
    const tag = gateTag(power);
    const wiring = wiringText(detail.condition, gate.node, trace);
    return (
      <div className={`sect insp${pinned ? ' pinned' : ''}`} aria-live="polite">
        <h3>
          Gate G{gate.number} <span className={TAG_CLASS[tag]}>{tag}</span>
          {unpin}
        </h3>
        <div className="insp-t">{gateSentence(gate.text)}</div>
        <dl className="kv">
          <dt>Reads</dt>
          <dd className="mono">{insight.reads}</dd>
          <dt>This tx</dt>
          <dd>
            <ol className="steps">
              {insight.steps.map((step, i) => (
                <li key={i} className="mono">
                  {step}
                </li>
              ))}
            </ol>
          </dd>
          <dt>Test</dt>
          <dd className="mono">{insight.test}</dd>
          <dt>Power</dt>
          <dd>{powerText(power)}</dd>
          {wiring && (
            <>
              <dt>Wiring</dt>
              <dd>{wiring}</dd>
            </>
          )}
        </dl>
        {insight.note && (
          <p className="note abn">
            <Icon name="warn" />
            <span>{insight.note}</span>
          </p>
        )}
      </div>
    );
  }

  const live = gates.filter((g) => g.pout);
  const dark = gates.filter((g) => g.pin && !g.pout);
  const at = (delay: number): CSSProperties | undefined =>
    playing ? ({ '--d': `${delay.toFixed(3)}s` } as CSSProperties) : undefined;
  const row = (g: (typeof gates)[number]) => (
    <li key={g.number} style={g.pout ? at(g.delay) : undefined}>
      <button type="button" className="chainbtn" onClick={() => g.node.id !== null && onInspect(g.node.id)}>
        <span className="gn">G{g.number}</span>
        {gateSentence(g.text)}
        <span className="val">{observedText(g.node.id === null ? undefined : trace[g.node.id])}</span>
      </button>
    </li>
  );
  return (
    <div className={`sect insp${playing ? ' anim' : ''}${pinned ? ' pinned' : ''}`} aria-live="polite">
      <h3>
        Coil <Ident rule={detail.rule} />{' '}
        {scene.coil.lit ? <span className="tag pw">ENERGISED</span> : <span className="tag">NO MATCH</span>}
        {pinned && unpin}
      </h3>
      <div className="insp-t">
        {scene.coil.lit
          ? `Power reached the coil through ${live.length} ${plural(live.length, 'gate')}`
          : 'Power did not reach the coil'}
      </div>
      <ol className="chain">
        {live.map(row)}
        {scene.coil.lit && (
          <li className="end" style={at(scene.coil.delay)}>
            COIL {detail.rule.tag} · MATCH
          </li>
        )}
      </ol>
      {dark.length > 0 && (
        <>
          <p className="lbl dark-h">Branches that stayed dark</p>
          <ul className="chain dark">{dark.map(row)}</ul>
        </>
      )}
    </div>
  );
}

export function TraceView({
  detail,
  describer,
  playKey,
  inspect,
  pinned,
  onInspect,
  onUnpin,
  onJump,
  onCopy,
}: {
  detail: MatchDetail;
  describer: Describer;
  playKey: string | null;
  inspect: Inspect;
  pinned: boolean;
  onInspect: (pick: Inspect) => void;
  onUnpin: () => void;
  onJump: (matchId: number) => void;
  onCopy: (text: string) => void;
}) {
  const [ref, width] = useBoxWidth<HTMLDivElement>();
  const { transaction: tx, transfer, rule, trace } = detail;
  // Without a trace there is no circuit to draw: drawn anyway, it would read as
  // a coil power never reached, on a match that was recorded.
  const scene = useScene(trace ? detail.condition : null, trace, width, describer);
  const { playing, key } = usePlayback(playKey, scene, width);
  const labels = labelsOf(detail);
  const labelOf = (address: string | null) => (address ? labels.get(address) ?? null : null);
  const token = transfer?.token;

  return (
    <>
      <div className="trace-h">
        <h3 className="rname">
          <Ident rule={rule} big /> <span>{rule.name}</span>
        </h3>
        <div className="sub">
          Coil energised at block {groupDigits(String(tx.block_number))}, tx {tx.transaction_index} ·{' '}
          {utcDateTime(tx.block_timestamp)}
        </div>
      </div>
      {trace ? (
        <>
          <div className="rungbox trace-box" ref={ref}>
            {scene && (
              <CircuitSvg
                key={key}
                scene={scene}
                tag={rule.tag}
                glyph={rule.glyph}
                big
                playing={playing}
                mode="inspect"
                selected={inspect}
                onPick={onInspect}
                label={`Circuit ${rule.tag} for this transaction. The lit path shows the gates that carried power. Select a gate to inspect it.`}
                boxWidth={width}
              />
            )}
          </div>
          <div className="legend">
            <span>
              <i className="sw" />
              Carried power
            </span>
            <span>
              <i className="sw off" />
              Blocked
            </span>
            <span>
              <i className="sw q" />
              No data
            </span>
            <span className="lg-hint">Select a gate or the coil to inspect it</span>
          </div>
          {scene && (
            <Inspector
              detail={detail}
              trace={trace}
              scene={scene}
              inspect={inspect}
              pinned={pinned}
              playing={playing}
              describer={describer}
              onInspect={onInspect}
              onUnpin={onUnpin}
            />
          )}
        </>
      ) : (
        <div className="sect">
          <div className="note">
            <Icon name="info" />
            <span>
              <b>No trace for this match.</b> The engine doesn't record which gates carried power yet, so the circuit
              isn't drawn.
            </span>
          </div>
        </div>
      )}
      {detail.also_matched.length > 0 && (
        <div className="sect">
          <h3>Also energised by this transaction</h3>
          <div className="also">
            {detail.also_matched.map((other) => (
              <button type="button" key={other.match_id} className="chip" onClick={() => onJump(other.match_id)}>
                <Ident rule={other.rule} /> {other.rule.name}
              </button>
            ))}
          </div>
        </div>
      )}
      {transfer && token && (
        <div className="sect">
          <h3>Token transfer</h3>
          <div className="big">
            {token.decimals === null ? `raw ${groupDigits(transfer.raw_value)}` : formatUnits(transfer.raw_value, token.decimals)}
            <span className="u">{token.symbol ?? 'Unknown token'}</span>
          </div>
          <dl className="kv facts">
            <dt>From</dt>
            <dd>
              <Address chain={tx.chain} address={transfer.from_address} label={labelOf(transfer.from_address)} />
            </dd>
            <dt>To</dt>
            <dd>
              <Address chain={tx.chain} address={transfer.to_address} label={labelOf(transfer.to_address)} />
            </dd>
            <dt>Token</dt>
            <dd>
              {token.symbol ? (
                <>
                  {token.name ?? token.symbol} <span className="tag">{token.symbol}</span>
                </>
              ) : (
                <span className="tag abn">Unrecognised</span>
              )}{' '}
              <span className="mono">{short(token.address)}</span>
            </dd>
            <dt>Raw amount</dt>
            <dd className="mono">
              {groupDigits(transfer.raw_value)}
              {token.decimals !== null ? ` · ${token.decimals} decimals` : ''}
            </dd>
            <dt>Proof</dt>
            <dd>
              <span className="tag">
                {transfer.source === 'calldata' ? 'Calldata' : 'Log'} · {transfer.verified ? 'verified' : 'unverified'}
              </span>
            </dd>
          </dl>
          <div className="notes">
            {!token.symbol ? (
              <div className="note abn">
                <Icon name="warn" />
                <span>The token catalog doesn't recognise this contract, so the token name and decimals are unknown.</span>
              </div>
            ) : token.decimals === null ? (
              <div className="note abn">
                <Icon name="warn" />
                <span>
                  {token.symbol}'s decimals haven't been read from the contract yet. The amount is shown raw rather than
                  guessed.
                </span>
              </div>
            ) : null}
            {!transfer.verified && (
              <div className="note">
                <Icon name="info" />
                <span>
                  <b>Read from {transfer.source === 'calldata' ? 'calldata' : 'the transfer log'}, not yet verified.</b>{' '}
                  The transaction called <span className="mono">{tx.method ?? 'a transfer method'}</span> on this contract.
                  The receipt has not been checked, so the call may have reverted.
                </span>
              </div>
            )}
          </div>
        </div>
      )}
      <div className="sect">
        <h3>Transaction</h3>
        <dl className="kv">
          <dt>Hash</dt>
          <dd>
            {EXPLORERS[tx.chain] ? (
              <a className="mono" href={`${EXPLORERS[tx.chain]}/tx/${tx.hash}`} target="_blank" rel="noopener noreferrer">
                {tx.hash}
              </a>
            ) : (
              <span className="mono">{tx.hash}</span>
            )}{' '}
            <button type="button" className="copy" onClick={() => onCopy(tx.hash)} aria-label="Copy transaction hash">
              <Icon name="copy" />
            </button>
          </dd>
          <dt>Block</dt>
          <dd className="mono">
            {groupDigits(String(tx.block_number))} · index {tx.transaction_index}
          </dd>
          <dt>Sender</dt>
          <dd>
            <Address chain={tx.chain} address={tx.from_address} label={labelOf(tx.from_address)} />
          </dd>
          <dt>To</dt>
          <dd>
            <Address chain={tx.chain} address={tx.to_address} label={labelOf(tx.to_address)} />
          </dd>
          <dt>ETH value</dt>
          <dd className="mono">{formatUnits(tx.value, 18, 4)} ETH</dd>
          <dt>Method</dt>
          <dd className="mono">{tx.method ?? tx.input_selector ?? 'plain transfer'}</dd>
          <dt>Decode status</dt>
          <dd>
            <span className={tx.decode_status === 'DECODED' ? 'tag pw' : 'tag'}>
              {
                {
                  DECODED: 'Decoded',
                  UNABLE_TO_DECODE: 'Unable to decode',
                  INGESTED: 'Ingested',
                  PROCESSING: 'Processing',
                }[tx.decode_status]
              }
            </span>
          </dd>
        </dl>
      </div>
    </>
  );
}
