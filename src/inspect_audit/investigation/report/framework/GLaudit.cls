% Generality Labs audit-report page layout.
% Adapted from Davide Gamba's HL-LHC EDMS-like class (2017--2022) to Generality Lab's auditing framework by Laurence Wroe.
% Defines page geometry, report metadata, section pagination, and branded headers.
% Audit criteria and finding boxes are defined in auditframework.sty.
\NeedsTeXFormat{LaTeX2e}
\ProvidesClass{GLaudit}[2026/09/16 Generality Labs audit-report layout]
\DeclareOption*{\PassOptionsToClass{\CurrentOption}{article}}
\ProcessOptions\relax
\LoadClass{article}

\RequirePackage{etoolbox}
\RequirePackage{changepage}
\RequirePackage[explicit]{titlesec}
\RequirePackage{caption}
\RequirePackage{tabularx}
\RequirePackage{graphicx}
\RequirePackage{tikz}
\RequirePackage{fancyhdr}
\usetikzlibrary{positioning,calc}

% Page geometry: margins, content inset, and frame stroke.
\newlength{\gleftspace}
\newlength{\grightspace}
\newlength{\gtopspace}
\newlength{\gbottomspace}
\newlength{\gtextmargin}
\newlength{\gthicklinewidth}
\setlength{\gleftspace}{16mm}
\setlength{\grightspace}{16mm}
\setlength{\gtopspace}{34mm}
\setlength{\gbottomspace}{21mm}
\setlength{\gtextmargin}{5mm}
\setlength{\gthicklinewidth}{1.pt}

% Report metadata.
\newcommand*{\reporttype}[1]{\def\rreporttype{#1}}
% Header text defaults to the report type and framework version.
\newcommand*{\headertype}[1]{\def\hheadertype{#1}}
\newcommand*{\evalname}[1]{\def\eevalname{#1}}
\newcommand*{\evalscore}[1]{\def\eevalscore{#1}}
\newcommand*{\reportno}[1]{\def\rreportno{#1}}
\newcommand*{\commitref}[1]{\def\ccommitref{#1}}
\newcommand*{\auditframework}[1]{\def\aauditframework{#1}}
% Defaults for documents that omit individual metadata fields.
\reporttype{---}
\headertype{\rreporttype: \aauditframework}
\evalname{---}
\evalscore{---}
\reportno{---}
\commitref{---}
\auditframework{---}

% Each report section has its own page count; auditframework.sty adds the total.
\def\ppartname{}
\def\ppartkey{front}
\newcommand{\auditpart}[2]{%
  \label{partlast:\ppartkey}%
  \clearpage
  \gdef\ppartname{#1}%
  \gdef\ppartkey{#2}%
  \setcounter{page}{1}%
}
% Call \auditpartsend once, just before \end{document} content ends,
% to record the last page of the final part.
\newcommand{\auditpartsend}{\label{partlast:\ppartkey}}
\newcommand{\partfooter}{%
  \ifdefempty{\ppartname}{}{\ppartname\ --\ }%
  Page \thepage\ of \pageref*{partlast:\ppartkey}%
}

% Main report typography; brandTeal is supplied by auditframework.sty.
\newenvironment{maincontentstyled}%
  {%
  \titleformat{\section}{\normalfont\large\bfseries}{\makebox[1cm][l]{\thesection}}{0pt}{\MakeUppercase{##1}}
  \titlespacing*{\section}{-1cm}{12pt}{10pt}
  \titleformat{\subsection}{\normalfont\bfseries}{}{0pt}{%
    \noindent\colorbox{brandTeal}{\parbox{\dimexpr\linewidth-2\fboxsep\relax}{%
      \textcolor{white}{\makebox[9mm][l]{\thesubsection}\MakeUppercase{##1}}}}}
  \titlespacing*{\subsection}{0pt}{14pt}{8pt}
  \titleformat{\subsubsection}{\normalfont\large\bfseries}{\makebox[1cm][l]{\thesubsubsection}}{0pt}{##1}
  \titlespacing*{\subsubsection}{-1cm}{12pt}{12pt}
  \setlength{\parindent}{0pt}
  \setlength{\parskip}{2pt}%
  \captionsetup{labelfont={bf},textfont={bf},belowskip={2.88pt},aboveskip={2.88pt},width=0.9\columnwidth}
  \begin{adjustwidth}{1.5cm}{\gtextmargin}}%
  {\end{adjustwidth}}

% Weighted tabularx columns: left, right, and centred.
\newcolumntype{L}[1]{>{\hsize=#1\hsize\raggedright\arraybackslash}X}%
\newcolumntype{R}[1]{>{\hsize=#1\hsize\raggedleft\arraybackslash}X}%
\newcolumntype{C}[1]{>{\hsize=#1\hsize\centering\arraybackslash}X}%

% Page layout and branded header/footer.
\RequirePackage[
  left=\gleftspace, right=\grightspace,
  top=\gtopspace, bottom=\gbottomspace,
  headheight=22mm, headsep=6mm,
  footskip=8mm
]{geometry}

\fancypagestyle{generalitylabs}{%
  \fancyhf{} % clear all six fields
  \renewcommand{\headrulewidth}{0pt} % no head rule
  \renewcommand{\footrulewidth}{0pt} % no foot rule
  % Generality Labs logo.
  \lhead{
    \hspace{5mm}
    \includegraphics[height=1.2cm]{assets/Generality_Labs.png}
  }
  % Add reference numbers on top right header
  \rhead{
    \begin{tikzpicture}[overlay,remember picture]
        % lower box: REPORT TYPE | REPORT NO.
        \node (lowerRect) [rectangle, draw, text width=68mm, minimum height=10mm,
                line width=\gthicklinewidth, rounded corners=7pt, black, anchor=south east, align=center,
                font=\fontsize{10}{12}\selectfont]
            at (0mm,-1mm) {\begin{tabular}{
                >{\centering\let\newline\\\arraybackslash\hspace{0pt}}m{28mm} | %
                >{\centering\let\newline\\\arraybackslash\hspace{0pt}}m{30mm}}
                    REPORT TYPE 	& REPORT NO. \\
                    \textbf{\hheadertype}	& \textbf{\rreportno}
                \end{tabular}};
        % upper box: EVAL | COMMIT REF
        \node (upperRect) [rectangle, draw, text width=68mm, minimum height=10mm,
                line width=\gthicklinewidth, rounded corners=7pt, black, anchor=south east, align=center,
                font=\fontsize{10}{12}\selectfont]
           at (lowerRect.north east) [yshift=1pt] {\begin{tabular}{
                >{\centering\let\newline\\\arraybackslash\hspace{0pt}}m{28mm} | %
                >{\centering\let\newline\\\arraybackslash\hspace{0pt}}m{30mm}}
                    EVAL 	& COMMIT REF \\
                    \textbf{\eevalname}	& \textbf{\ccommitref}
                \end{tabular}};
    \end{tikzpicture}
  }
  % add the page frame within the head
  \fancyhead[CO]{
  \begin{tikzpicture}[remember picture, overlay, >=latex]
        \draw [line width=\gthicklinewidth,rounded corners=4mm, black]
            ($ (current page.north west) + (\gleftspace,-\gtopspace+4mm) $)
            rectangle
            ($ (current page.south east) + (-\grightspace,\gbottomspace-4mm)   $);
    \end{tikzpicture}
  }
  % Add per-part page numbers on bottom left
  \fancyfoot[LO]{
   \hspace{5mm}\partfooter
  }
  % Mirror the frame and section footer for two-sided documents.
  \if@twoside
    \fancyhead[CE]{
    \begin{tikzpicture}[remember picture, overlay, >=latex]
          \draw [line width=\gthicklinewidth,rounded corners=4mm, black]
              ($ (current page.north west) + (\grightspace,-\gtopspace+4mm) $)
              rectangle
              ($ (current page.south east) + (-\gleftspace,\gbottomspace-4mm)   $);
      \end{tikzpicture}
    }
    \fancyfoot[RE]{
       \partfooter\hspace{5mm}
    }
  \fi
}

\AtBeginDocument{\pagestyle{generalitylabs}}
