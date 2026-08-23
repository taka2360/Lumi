; Uninstaller hooks (roadmap 2g / docs/contracts/privacy.md §5).
;
; **Uninstalling does not delete what Lumi remembered unless the user says so.**
; The memory database is the user's own data, and removing it as a side effect of
; removing the program would destroy something they never asked to lose — including
; in the ordinary case of reinstalling.
;
; The question is asked **after** the program is gone, defaulting to "no": the default
; of a destructive question is the one that can be undone by asking again, and the
; other way round there is nothing left to ask about.

!macro NSIS_HOOK_POSTUNINSTALL
  ; Silent uninstalls (`/S`, used by upgrades and by management tooling) never delete
  ; user data. There is nobody there to answer, and **silence is not consent.**
  IfSilent lumi_keep_data

  MessageBox MB_YESNO|MB_ICONQUESTION|MB_DEFBUTTON2 \
    "Also delete what Lumi remembered?$\r$\n$\r$\nThis removes the memory database, the conversation history and the logs in:$\r$\n$LOCALAPPDATA\Lumi$\r$\n$\r$\nThis cannot be undone." \
    /SD IDNO IDYES lumi_delete_data IDNO lumi_keep_data

  lumi_delete_data:
    ; The whole data root, which is where Core keeps memory.db, events.db, audit.db,
    ; the fetched models and the settings file (core/lumi/paths.py).
    RMDir /r "$LOCALAPPDATA\Lumi"
    Goto lumi_done

  lumi_keep_data:
    DetailPrint "Lumi's memory was kept in $LOCALAPPDATA\Lumi"

  lumi_done:
!macroend
