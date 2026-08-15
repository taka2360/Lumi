import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { Credits } from "./Credits";
import "./credits.css";

const container = document.getElementById("root");
if (!container) {
  throw new Error("#root が見つからない");
}

createRoot(container).render(
  <StrictMode>
    <Credits />
  </StrictMode>,
);
