/** @type {import('tailwindcss').Config} */
export default {
  darkMode: "class",
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        navy: {
          900: "#0b1f33",
          800: "#12304a",
          700: "#1b3f5c",
        },
        teal: {
          600: "#0f766e",
          500: "#0d9488",
          100: "#ccfbf1",
        },
      },
    },
  },
  plugins: [],
};
