/**
 * クレジットとライセンスの画面（トレイ → クレジット）。
 *
 * 設計 → docs/licensing.md §6 / docs/architecture/ui.md
 *
 * **この画面は Core にも外部にも接続しない。** ライセンス文書の提示は Lumi の
 * 動作状態と無関係な義務であり、Core が落ちていても読めなければならない。
 * したがってここから `../core/*` を import しない。
 */

import {
  BUNDLED,
  CREDIT_EXAMPLES,
  EXTERNAL,
  LICENSES,
  LUMI,
  PROHIBITIONS,
  type SectionId,
} from "./content";

function Section({
  id,
  title,
  lead,
  children,
}: {
  id: SectionId;
  title: string;
  lead?: string;
  children: React.ReactNode;
}) {
  return (
    <section className="credits__section" id={id}>
      <h2 className="credits__heading">{title}</h2>
      {lead && <p className="credits__lead">{lead}</p>}
      {children}
    </section>
  );
}

export function Credits() {
  return (
    <main className="credits">
      <h1 className="credits__title">クレジットとライセンス</h1>

      <Section id="lumi" title={LUMI.name} lead={LUMI.description}>
        <p className="credits__lead">ライセンス: {LUMI.license}</p>
      </Section>

      <Section
        id="bundled"
        title="Lumi に同梱しているソフトウェア"
        lead="Lumi のインストーラに含まれるものです。"
      >
        {BUNDLED.map((component) => (
          <div key={component.component} className="credits__group">
            <h3 className="credits__subheading">
              {component.component} <span className="credits__note">{component.note}</span>
            </h3>
            <table className="credits__table">
              <tbody>
                {component.dependencies.map((dep) => (
                  <tr key={dep.name}>
                    <td>{dep.name}</td>
                    <td className="credits__version">{dep.version}</td>
                    <td>{dep.license}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ))}
      </Section>

      <Section
        id="external"
        title="Lumi に同梱していないソフトウェア"
        lead="Lumi はこれらを配布していません。取得もインストールも、あなたの PC の上で行われます。使っていないものも含めて載せています。"
      >
        {EXTERNAL.map((external) => (
          <div key={external.name} className="credits__group">
            <h3 className="credits__subheading">{external.name}</h3>
            <p className="credits__lead">ライセンス: {external.license}</p>
            <p className="credits__note">適用されるとき: {external.appliesWhen}</p>
            <ul className="credits__list">
              {external.obligations.map((obligation) => (
                <li key={obligation}>{obligation}</li>
              ))}
            </ul>
            <p className="credits__note">入手元: {external.source}</p>
          </div>
        ))}
      </Section>

      <Section
        id="voice"
        title="音声のクレジット表記"
        lead="VOICEVOX の音源を使って音声を公開するときは、次の形でクレジットを表記してください。"
      >
        <ul className="credits__list">
          {CREDIT_EXAMPLES.map((example) => (
            <li key={example}>
              <code>{example}</code>
            </li>
          ))}
        </ul>
        <p className="credits__note">ACML の音声合成モデルではクレジット表記は任意です。</p>
      </Section>

      <Section
        id="prohibitions"
        title="禁止されていること"
        lead="Lumi で作った音声にも、元になった音源・モデルの規約がそのまま適用されます。"
      >
        {PROHIBITIONS.map((set) => (
          <div key={set.source} className="credits__group">
            <h3 className="credits__subheading">{set.source}</h3>
            <p className="credits__note">適用されるとき: {set.appliesWhen}</p>
            <ul className="credits__list">
              {set.items.map((item) => (
                <li key={item}>{item}</li>
              ))}
            </ul>
          </div>
        ))}
      </Section>

      <Section id="licenses" title="ライセンス全文">
        {LICENSES.map((license) => (
          <details key={license.id} className="credits__license">
            <summary className="credits__summary">{license.title}</summary>
            <p className="credits__note">{license.note}</p>
            <pre className="credits__text">{license.text}</pre>
          </details>
        ))}
      </Section>
    </main>
  );
}
