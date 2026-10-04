// Text content for the real flow: role -> connect Telegram account via QR
// login -> optional 2FA password.

const STEP_HEADERS = [
  {
    title: "Обери посаду",
    description: "Це потрібно один раз — щоб бот знав, хто ти в команді, і показував потрібні функції.",
  },
  {
    title: "Підключи аккаунт",
    description: "Відскануй QR-код іншим пристроєм з Telegram (Налаштування → Пристрої → Підключити пристрій) або відкрий посилання на цьому ж телефоні.",
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
