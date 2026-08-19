/**
 * The credits and licenses screen (tray → credits).
 *
 * Design → docs/licensing.md §6 / docs/architecture/ui.md
 *
 * **This screen connects neither to Core nor externally.** Presenting license
 * documents is an obligation independent of Lumi's runtime state, and must be
 * readable even if Core is down. So nothing here imports `../core/*`.
 */

import { translate } from "../i18n";
import { useStandaloneLocale } from "../i18n/standalone";
import {
  BUNDLED,
  CREDIT_EXAMPLES,
  EXTERNAL,
  LICENSES,
  LUMI,
  PROHIBITIONS,
  type SectionId,
  THIRD_PARTY,
} from "./content";
import { creditText } from "./localize";

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
  const locale = useStandaloneLocale();
  const ct = (text: string) => creditText(locale, text);
  return (
    <main className="credits">
      <h1 className="credits__title">{translate(locale, "credits.title")}</h1>

      <Section id="lumi" title={LUMI.name} lead={ct(LUMI.description)}>
        <p className="credits__lead">
          {translate(locale, "credits.license")}: {LUMI.license}
        </p>
      </Section>

      <Section
        id="bundled"
        title={translate(locale, "credits.bundled.title")}
        lead={translate(locale, "credits.bundled.lead")}
      >
        {BUNDLED.map((component) => (
          <div key={component.component} className="credits__group">
            <h3 className="credits__subheading">
              {ct(component.component)} <span className="credits__note">{component.note}</span>
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
        title={translate(locale, "credits.external.title")}
        lead={translate(locale, "credits.external.lead")}
      >
        {EXTERNAL.map((external) => (
          <div key={external.name} className="credits__group">
            <h3 className="credits__subheading">{ct(external.name)}</h3>
            <p className="credits__lead">
              {translate(locale, "credits.license")}: {ct(external.license)}
            </p>
            <p className="credits__note">
              {translate(locale, "credits.appliesWhen")}: {ct(external.appliesWhen)}
            </p>
            <ul className="credits__list">
              {external.obligations.map((obligation) => (
                <li key={obligation}>{ct(obligation)}</li>
              ))}
            </ul>
            <p className="credits__note">
              {translate(locale, "credits.source")}: {external.source}
            </p>
          </div>
        ))}
      </Section>

      <Section
        id="voice"
        title={translate(locale, "credits.voice.title")}
        lead={translate(locale, "credits.voice.lead")}
      >
        <ul className="credits__list">
          {CREDIT_EXAMPLES.map((example) => (
            <li key={example}>
              <code>{example}</code>
            </li>
          ))}
        </ul>
        <p className="credits__note">{translate(locale, "credits.voice.acml")}</p>
      </Section>

      <Section
        id="prohibitions"
        title={translate(locale, "credits.prohibitions.title")}
        lead={translate(locale, "credits.prohibitions.lead")}
      >
        {PROHIBITIONS.map((set) => (
          <div key={set.source} className="credits__group">
            <h3 className="credits__subheading">{ct(set.source)}</h3>
            <p className="credits__note">
              {translate(locale, "credits.appliesWhen")}: {ct(set.appliesWhen)}
            </p>
            <ul className="credits__list">
              {set.items.map((item) => (
                <li key={item}>{ct(item)}</li>
              ))}
            </ul>
          </div>
        ))}
      </Section>

      <Section
        id="third-party"
        title={translate(locale, "credits.thirdParty.title")}
        lead={translate(locale, "credits.thirdParty.lead", { count: THIRD_PARTY.total })}
      >
        {THIRD_PARTY.ecosystems.map((ecosystem) => (
          <details key={ecosystem.name} className="credits__license">
            <summary className="credits__summary">
              {ecosystem.name}{" "}
              <span className="credits__note">
                {translate(locale, "credits.packages", { count: ecosystem.packages.length })}
              </span>
            </summary>
            <table className="credits__table">
              <tbody>
                {ecosystem.packages.map((dep) => (
                  <tr key={`${dep.name}@${dep.version}`}>
                    <td>{dep.name}</td>
                    <td className="credits__version">{dep.version}</td>
                    <td>{dep.license}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </details>
        ))}
      </Section>

      <Section id="licenses" title={translate(locale, "credits.licenses.title")}>
        {LICENSES.map((license) => (
          <details key={license.id} className="credits__license">
            <summary className="credits__summary">{license.title}</summary>
            <p className="credits__note">{ct(license.note)}</p>
            <pre className="credits__text">{license.text}</pre>
          </details>
        ))}
      </Section>
    </main>
  );
}
