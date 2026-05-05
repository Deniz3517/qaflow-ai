const { defineConfig } = require("cypress");

module.exports = defineConfig({
  projectId: "sporthub-qaflow",
  e2e: {
    baseUrl: "http://localhost:3001",
    specPattern: "cypress/e2e/**/*.cy.js",
    supportFile: "cypress/support/e2e.js",
    fixturesFolder: "cypress/fixtures",
    screenshotsFolder: "cypress/screenshots",
    videosFolder: "cypress/videos",
    video: false,
    screenshotOnRunFailure: true,
    viewportWidth: 1280,
    viewportHeight: 800,
    defaultCommandTimeout: 4000,
    pageLoadTimeout: 10000,
    retries: { runMode: 0, openMode: 0 },
    setupNodeEvents(on, config) {
      // Per-test logging — emits a single JSON line per assertion to stdout
      // so the QAFLOW backend can stream progress over stdout.
      on("task", {
        log(payload) {
          // eslint-disable-next-line no-console
          console.log(`QAFLOW_LOG ${JSON.stringify(payload)}`);
          return null;
        },
      });
      return config;
    },
  },
});
