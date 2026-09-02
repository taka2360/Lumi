/**
 * The credits and licenses screen (tray or Stage action menu → credits).
 *
 * Design → docs/licensing.md §6 / docs/architecture/ui.md
 *
 * **This screen connects neither to Core nor externally.** Presenting license
 * documents is an obligation independent of Lumi's runtime state, and must be
 * readable even if Core is down. So nothing here imports `../core/*`.
 */

import { type MessageKey, translate } from "../i18n";
import { useStandaloneLocale } from "../i18n/standalone";
import {
  BUNDLED,
  CREDIT_EXAMPLES,
  ECOSYSTEM_LABEL,
  EXTERNAL,
  LICENSES,
  LUMI,
  PROHIBITIONS,
  type SectionId,
  THIRD_PARTY,
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
    <section className="document__section" id={id}>
      <h2 className="document__heading">{title}</h2>
      {lead && <p className="credits__lead">{lead}</p>}
      {children}
    </section>
  );
}

export function Credits() {
  const locale = useStandaloneLocale();
  const t = (key: MessageKey) => translate(locale, key);
  return (
    <main className="document credits">
      <h1 className="document__title">{translate(locale, "credits.title")}</h1>

      <Section id="lumi" title={LUMI.name} lead={t(LUMI.descriptionKey)}>
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
          <div key={component.componentKey} className="credits__group">
            <h3 className="credits__subheading">
              {t(component.componentKey)} <span className="credits__note">{component.note}</span>
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
          <div key={external.nameKey} className="credits__group">
            <h3 className="credits__subheading">{t(external.nameKey)}</h3>
            <p className="credits__lead">
              {translate(locale, "credits.license")}: {t(external.licenseKey)}
            </p>
            <p className="credits__note">
              {translate(locale, "credits.appliesWhen")}: {t(external.appliesWhenKey)}
            </p>
            <ul className="credits__list">
              {external.obligationKeys.map((obligation) => (
                <li key={obligation}>{t(obligation)}</li>
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
          <div key={set.sourceKey} className="credits__group">
            <h3 className="credits__subheading">{t(set.sourceKey)}</h3>
            <p className="credits__note">
              {translate(locale, "credits.appliesWhen")}: {t(set.appliesWhenKey)}
            </p>
            <ul className="credits__list">
              {set.itemKeys.map((item) => (
                <li key={item}>{t(item)}</li>
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
          <details key={ecosystem.id} className="credits__license">
            <summary className="credits__summary">
              {t(ECOSYSTEM_LABEL[ecosystem.id])}{" "}
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
            <p className="credits__note">{t(license.noteKey)}</p>
            <pre className="credits__text">{license.text}</pre>
          </details>
        ))}
      </Section>
    </main>
  );
}
