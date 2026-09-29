// Milestone 5: launch an app bundle the way macOS launches a login item.
// The first Apple event is kAEOpenApplication with keyAEPropData =
// keyAELaunchedAsLogInItem ('lgit'), sent through public NSWorkspace API.
// Usage: osascript -l JavaScript scripts/m5_login_launch.js <App.app> [login|manual] [KEY=VALUE ...] [-- args...]
ObjC.import('AppKit');
function run(argv) {
  const [appPath, mode] = argv;
  const rest = argv.slice(2);
  const split = rest.indexOf('--');
  const envPairs = split < 0 ? rest : rest.slice(0, split);
  const args = split < 0 ? [] : rest.slice(split + 1);
  const four = (s) => ((s.charCodeAt(0) << 24) | (s.charCodeAt(1) << 16) | (s.charCodeAt(2) << 8) | s.charCodeAt(3)) >>> 0;
  const event = $.NSAppleEventDescriptor.appleEventWithEventClassEventIDTargetDescriptorReturnIDTransactionID(
    four('aevt'), four('oapp'), $.NSAppleEventDescriptor.nullDescriptor, -1, 0);
  if (mode === 'login') {
    event.setParamDescriptorForKeyword($.NSAppleEventDescriptor.descriptorWithEnumCode(four('lgit')), four('prdt'));
  }
  const env = $.NSMutableDictionary.dictionary;
  for (const pair of envPairs) {
    const i = pair.indexOf('=');
    env.setObjectForKey($(pair.slice(i + 1)), $(pair.slice(0, i)));
  }
  const config = $.NSWorkspaceOpenConfiguration.configuration;
  config.appleEvent = event;
  config.arguments = $(args);
  config.environment = env;
  config.createsNewApplicationInstance = true;
  config.activates = mode !== 'login';
  let done = false;
  let failure = null;
  $.NSWorkspace.sharedWorkspace.openApplicationAtURLConfigurationCompletionHandler(
    $.NSURL.fileURLWithPath(appPath), config, (app, error) => {
      if (error && !error.isNil()) failure = ObjC.unwrap(error.localizedDescription);
      done = true;
    });
  for (let i = 0; i < 100 && !done; i++) $.NSRunLoop.currentRunLoop.runUntilDate($.NSDate.dateWithTimeIntervalSinceNow(0.1));
  return failure ? `error: ${failure}` : done ? 'launched' : 'timeout';
}
