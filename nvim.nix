{ pkgs, ... }:
{
  programs.nixvim = {
    enable = true;
    defaultEditor = true;

    globals = {
      mapleader = " ";
      maplocalleader = ",";
      neovide_cursor_vfx_mode = "railgun";
      loaded_gzip = 1;
      loaded_netrwPlugin = 1;
      loaded_tarPlugin = 1;
      loaded_2html_plugin = 1;
      loaded_zipPlugin = 1;
    };

    opts = {
      number = true;
      relativenumber = true;
      signcolumn = "yes";
      spell = false;
      modeline = false;
      guifont = "JetBrainsMono_Nerd_Font:h12";
    };

    diagnostic.settings = {
      virtual_text = true;
      underline = true;
    };

    # Fallback until base16-nvim is set up from matugen.lua below.
    colorscheme = "habamax";

    extraPlugins = with pkgs.vimPlugins; [
      base16-nvim
      smear-cursor-nvim
      gh-nvim
      litee-nvim
    ];

    extraConfigLua = ''
      -- Written by Noctalia's neovim template into ~/.config/nvim/lua, reloaded on SIGUSR1.
      local ok, matugen = pcall(require, "matugen")
      if ok then matugen.setup() end

      if not vim.g.neovide then require("smear_cursor").setup({}) end

      require("litee.lib").setup()
      require("litee.gh").setup()

      local eyebuds = "/home/erik/uxstream/embedded/client/projects/eyebuds/Scripts/dap_eyebuds.lua"
      if vim.fn.filereadable(eyebuds) == 1 then
        local chunk, err = loadfile(eyebuds)
        if chunk then chunk() else vim.notify("eyebuds DAP load error: " .. tostring(err), vim.log.levels.ERROR) end
      end
    '';

    plugins = {
      web-devicons.enable = true;
      lualine.enable = true;
      bufferline.enable = true;
      neo-tree.enable = true;
      telescope.enable = true;
      which-key.enable = true;
      gitsigns.enable = true;
      blink-cmp.enable = true;
      nvim-autopairs.enable = true;
      toggleterm.enable = true;
      indent-blankline.enable = true;
      notify.enable = true;
      diffview.enable = true;
      dap.enable = true;
      # Draws mermaid blocks as images via kitty's graphics protocol, not in Neovide.
      snacks = {
        enable = true;
        settings.image.enabled = true;
      };

      render-markdown = {
        enable = true;
        settings.latex.enabled = false;
      };

      treesitter = {
        enable = true;
        settings.highlight.enable = true;
      };

      aerial = {
        enable = true;
        settings.backends = [ "lsp" "markdown" "man" ];
      };

      project-nvim = {
        enable = true;
        settings = {
          patterns = [ "Cargo.toml" ];
          lsp.enabled = true;
        };
      };

      lspconfig.enable = true;

      conform-nvim = {
        enable = true;
        settings = {
          formatters_by_ft.lua = [ "stylua" ];
          default_format_opts.lsp_format = "fallback";
          format_on_save.timeout_ms = 1000;
        };
      };
    };

    lsp = {
      servers.lua_ls.enable = true;
      inlayHints.enable = false;
    };

    extraPackages = with pkgs; [ stylua mermaid-cli imagemagick ];

    autoCmd = [
      {
        event = [ "InsertLeave" "BufEnter" ];
        desc = "Refresh codelens";
        callback.__raw = ''
          function(args)
            if #vim.lsp.get_clients({ bufnr = args.buf, method = "textDocument/codeLens" }) > 0 then
              vim.lsp.codelens.refresh({ bufnr = args.buf })
            end
          end
        '';
      }
    ];

    keymaps = [
      { mode = "n"; key = "]b"; action = "<cmd>BufferLineCycleNext<cr>"; options.desc = "Next buffer"; }
      { mode = "n"; key = "[b"; action = "<cmd>BufferLineCyclePrev<cr>"; options.desc = "Previous buffer"; }
      { mode = "n"; key = "<Leader>bd"; action = "<cmd>BufferLinePickClose<cr>"; options.desc = "Close buffer from tabline"; }
      { mode = "n"; key = "<Leader>e"; action = "<cmd>Neotree toggle<cr>"; options.desc = "Toggle explorer"; }
      { mode = "n"; key = "<Leader>ff"; action = "<cmd>Telescope find_files<cr>"; options.desc = "Find files"; }
      { mode = "n"; key = "<Leader>fw"; action = "<cmd>Telescope live_grep<cr>"; options.desc = "Find words"; }
      { mode = "n"; key = "gD"; action.__raw = "vim.lsp.buf.declaration"; options.desc = "Declaration of current symbol"; }
    ];
  };
}
