// Ported from DESIGN's step-header.js as an ES module. Text content is
// already accurate for the real flow (role -> connect Telegram account ->
// SMS code -> optional 2FA password), so it's carried over verbatim.

const STEP_HEADERS = [
  {
    title: "Обери посаду",
    description: "Це потрібно один раз — щоб бот знав, хто ти в команді, і показував потрібні функції.",
  },
  {
    title: "Підключи аккаунт",
    description: "Потрібно один раз авторизувати твій особистий Telegram, щоб бот міг створювати групи та додавати клієнтів від твого імені.",
  },
  {
    title: "Введи код із Telegram",
    description: "Код дійсний кілька хвилин — перевір повідомлення від Telegram.",
  },
  {
    title: "Введи пароль двоетапної перевірки",
    description: "На твоєму Telegram-акаунті ввімкнено хмарний пароль (2FA) — введи його, щоб завершити підключення.",
  },
];

export function renderStepHeader(titleEl, descriptionEl, step) {
  const data = STEP_HEADERS[step - 1];
  if (!data) return;

  titleEl.textContent = data.title;
  descriptionEl.textContent = data.description;
}
