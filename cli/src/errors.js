// Copied from google-docs-mcp@2fb4652 cli/src/workingcopy.js; only CliError kept, the working copy code dropped.
// The one error type gsheet reports as a message and an exit code.

/** A refusal or failure gsheet reports in one message and an exit code. */
export class CliError extends Error {
  name = "CliError";
  constructor(message, code = 1) {
    super(message);
    this.code = code;
  }
}
