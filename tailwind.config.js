/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,ts,jsx,tsx}"],
  theme: {
    extend: {
      colors: {
        tg: {
          bg: "var(--tg-bg)",
          text: "var(--tg-text)",
          hint: "var(--tg-hint)",
          link: "var(--tg-link)",
          button: "var(--tg-button-bg)",
          buttonText: "var(--tg-button-text)",
          secondaryBg: "var(--tg-secondary-bg)",
        },
      },
    },
  },
  plugins: [],
};
