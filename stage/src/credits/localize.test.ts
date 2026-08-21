import { expect, it } from "vitest";

import { BUNDLED, EXTERNAL, LICENSES, LUMI, PROHIBITIONS, THIRD_PARTY } from "./content";
import { creditText } from "./localize";

it("has English notices for every Japanese credit field", () => {
  const fields = [
    LUMI.description,
    ...BUNDLED.map((component) => component.component),
    ...EXTERNAL.flatMap((component) => [
      component.name,
      component.license,
      component.appliesWhen,
      ...component.obligations,
    ]),
    ...PROHIBITIONS.flatMap((set) => [set.source, set.appliesWhen, ...set.items]),
    ...LICENSES.map((license) => license.note),
    ...THIRD_PARTY.ecosystems.map((ecosystem) => ecosystem.name),
  ];

  for (const field of fields) {
    expect(creditText("en", field), field).not.toMatch(/[ぁ-んァ-ヶ一-龠]/);
  }
});
