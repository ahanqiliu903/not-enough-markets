import { homedir, userInfo } from "node:os";
import { join } from "node:path";
import { app } from "electron";
import { Logger } from "./logger";
import type { Secret } from "./redact";

/** The app's one logger, in `<userData>/logs/main.log` (DESK-020). */

let logger: Logger | null = null;

function osUser(): string {
  try {
    return userInfo().username;
  } catch {
    return "";
  }
}

export function log(): Logger {
  if (logger !== null) return logger;
  const userData = app.getPath("userData");
  logger = new Logger({ file: join(userData, "logs", "main.log") });
  const home = homedir();
  const user = osUser();
  logger.redactor.addProvider((): Secret[] => [
    // userData usually sits inside home: the longer value is replaced first.
    { value: userData, placeholder: "<userData>" },
    { value: home, placeholder: "~" },
    ...(user.length >= 3 ? [{ value: user, placeholder: "<user>" }] : []),
  ]);
  return logger;
}

export function logFile(): string {
  return log().file;
}
