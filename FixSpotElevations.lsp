;;; FixSpotElevations.lsp
;;;
;;; PROBLEM
;;; -------
;;; Revit's Spot Elevation tag ("+2.80 г.р.н." on line 1, "+2.73 г.р.б." on
;;; line 2) exports to DWG as a single MTEXT string with NO explicit line
;;; break between the two lines. In Revit's own renderer the values happen
;;; to soft-wrap into the correct 2-line layout, but AutoCAD substitutes a
;;; different font/metrics for the text style and wraps the same string
;;; differently (often into 3 cramped lines), so the value/label text ends
;;; up overlapping the spot-elevation symbol instead of sitting cleanly
;;; beside it.
;;;
;;; FIX
;;; ---
;;; The exported MTEXT content literally contains the marker text
;;; "\U+0433.\U+0440.\U+043D." (the label "г.р.н." encoded the way AutoCAD
;;; stores non-ASCII characters inside an MTEXT content string) immediately
;;; followed - with NO separator - by the second value (e.g. "+2.73"). That
;;; missing separator is exactly where Revit's own line break belongs. This
;;; script finds every MTEXT containing that marker and inserts an explicit
;;; MTEXT paragraph break (\P) right after it. This is a hard, font-
;;; independent line break, so the 2-line layout is guaranteed no matter what
;;; text style/font AutoCAD substitutes on the next export.
;;;
;;; The fix only edits the text CONTENT of the existing MTEXT entities - it
;;; does not explode them, so everything stays fully editable afterwards
;;; (double-click to edit text, grip-edit, etc. all keep working).
;;;
;;; USAGE
;;; -----
;;; After every DWG export from Revit, open the DWG in AutoCAD and run:
;;;   (load "FixSpotElevations.lsp")
;;;   MCPFIXSPOT
;;; It is safe to run more than once - entities already fixed (already
;;; containing "...г.р.н.\P...") are skipped.
;;;
;;; If your Revit spot elevation tags use a different label text than
;;; "г.р.н.", change MARKER below to match (it must be the literal label
;;; text that sits immediately before the second value, with no space).
;;;
;;; This copy also ships inside dwgmagic2 (next to tectonica.dll) so the
;;; per-sheet AutoCAD stage can load it and run MCPFIXSPOT automatically on
;;; each sheet before the merge stage, when the fix_spot_elevations setting
;;; is enabled. See dwgmagic/templates/sheet_script_template.tmpl.

(defun c:MCPFIXSPOT (/ ss n i en ed txt marker mlen pos already newtxt fixed)
  (setq marker "\\U+0433.\\U+0440.\\U+043D.") ; "г.р.н." as stored inside MTEXT
  (setq mlen (strlen marker))
  (setq fixed 0)
  (setq ss (ssget "X" (list (cons 0 "MTEXT"))))
  (if ss
    (progn
      (setq n (sslength ss))
      (setq i 0)
      (while (< i n)
        (setq en (ssname ss i))
        (setq ed (entget en))
        (setq txt (cdr (assoc 1 ed)))
        (if txt
          (progn
            (setq pos (vl-string-search marker txt))
            (if pos
              (progn
                (setq already (substr txt (+ pos mlen 1) 2))
                (if (/= already "\\P")
                  (progn
                    (setq newtxt (strcat (substr txt 1 (+ pos mlen)) "\\P" (substr txt (+ pos mlen 1))))
                    (entmod (subst (cons 1 newtxt) (assoc 1 ed) ed))
                    (setq fixed (1+ fixed))
                  )
                )
              )
            )
          )
        )
        (setq i (1+ i))
      )
    )
  )
  (princ (strcat "\nMCPFIXSPOT: fixed " (itoa fixed) " spot elevation MTEXT entities."))
  (princ)
)

(princ "\nFixSpotElevations loaded. Type MCPFIXSPOT to run.")
(princ)
