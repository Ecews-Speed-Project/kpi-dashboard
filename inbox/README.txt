Drop new source files here. When several files match, the newest one is used.
Each source can have its own folder (files directly in inbox\ still work):

- ndr\   pbs_all_<YYYY-MM-DD>*.xlsx  NDR PBS line list (active + inactive). Put the export date in the
                                    file name: it dates the history copy. After the next successful run,
                                    older pbs_all files are saved to data\history\sources and removed.
- eac\   ECEWS EAC Linelist_<date>.xlsx  EAC line list from the CDR team (password-protected: the
                                    password is in .env as EAC_FILE_PASSWORD). The newest date in the
                                    file name is used. Only ART / hospital numbers and EAC fields are read,
                                    never names or dates of birth.
- FY*Target*.xlsx             FY targets
- naomi*.xlsx                 Naomi PLHIV estimates
- dqa_sites*.csv, joint_ssv*.csv
The pbs_all and EAC files are client-level data: do not email them or copy them elsewhere.
