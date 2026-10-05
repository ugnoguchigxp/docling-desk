-- Native document export only; no UI scripting, keystrokes, or user-document saves.
on run argv
    set sourceFile to POSIX file (item 1 of argv)
    set targetFile to POSIX file (item 2 of argv)
    set workingName to item 3 of argv
    set workingDeck to missing value
    with timeout of 120 seconds
        tell application "Microsoft PowerPoint"
            try
                open sourceFile
                set workingDeck to presentation workingName
                set print hidden slides of print options of workingDeck to true
                set output type of print options of workingDeck to print slides
                set print fonts as graphics of print options of workingDeck to false
                save workingDeck in targetFile as save as PDF
                close workingDeck saving no
            on error errorText number errorNumber
                if workingDeck is not missing value then
                    try
                        close workingDeck saving no
                    end try
                end if
                error errorText number errorNumber
            end try
        end tell
    end timeout
end run
