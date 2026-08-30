/**
 * How to operate Lumi (tray or the action palette → help).
 *
 * Design → docs/architecture/ui.md §1 / [ADR-047]
 *
 * **This screen connects neither to Core nor externally**, like credits — and for a
 * reason of its own. The gestures it explains are how someone reaches the setup screen's
 * controls, and that screen is on display exactly when Core has not come up. So nothing
 * here imports `../core/*`.
 *
 * It exists because **right-clicking the character is not a discoverable gesture**. That
 * is the cost ADR-047 accepted; this page, and the tray item beside it, are what pay it.
 */

import { ACTION_ITEMS } from "../actions/items";
import { translate } from "../i18n";
import { useStandaloneLocale } from "../i18n/standalone";
import { GESTURES } from "./content";

export function Help() {
  const locale = useStandaloneLocale();
  const t = (key: Parameters<typeof translate>[1]) => translate(locale, key);

  return (
    <main className="document help">
      <h1 className="document__title">{t("help.title")}</h1>

      <section className="document__section">
        <h2 className="document__heading">{t("help.gestures.title")}</h2>
        <p className="help__lead">{t("help.gestures.lead")}</p>
        <table className="help__table">
          <thead>
            <tr>
              <th scope="col">{t("help.gestures.column.gesture")}</th>
              <th scope="col">{t("help.gestures.column.effect")}</th>
            </tr>
          </thead>
          <tbody>
            {GESTURES.map((row) => (
              <tr key={row.gesture}>
                <th scope="row" className="help__gesture">
                  {t(row.gesture)}
                </th>
                <td>{t(row.effect)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      <section className="document__section">
        <h2 className="document__heading">{t("help.menu.title")}</h2>
        <p className="help__lead">{t("help.menu.lead")}</p>
        <ul className="help__actions">
          {ACTION_ITEMS.map((item) => (
            <li key={item.name} className="help__action">
              {/* The same glyph as the button itself, from the same list. It carries no
                  meaning on its own, so the name beside it is what names the action. */}
              <span className="help__glyph" aria-hidden="true">
                {item.glyph}
              </span>
              <span className="help__action-name">{t(item.labelKey)}</span>
              <span className="help__action-about">{t(item.aboutKey)}</span>
            </li>
          ))}
        </ul>
      </section>

      <section className="document__section">
        <h2 className="document__heading">{t("help.tray.title")}</h2>
        <p className="help__lead">{t("help.tray.body")}</p>
      </section>
    </main>
  );
}
