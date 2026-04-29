module axi_lite_slave (
    input  logic         clk,
    input  logic         rst,

    input  logic         awvalid,
    output logic         awready,
    input  logic [31:0]  awaddr,

    input  logic         wvalid,
    output logic         wready,
    input  logic [31:0]  wdata,

    output logic         bvalid,
    input  logic         bready,

    input  logic         arvalid,
    output logic         arready,
    input  logic [31:0]  araddr,

    output logic         rvalid,
    input  logic         rready,
    output logic [31:0]  rdata
);

logic [31:0] reg0;

always_ff @(posedge clk) begin
    if (rst) begin
        reg0   <= 32'd0;
        bvalid <= 1'b0;
        rvalid <= 1'b0;
    end
    else begin
        if (awvalid && wvalid) begin
            reg0   <= wdata;
            bvalid <= 1'b1;
        end

        if (bready)
            bvalid <= 1'b0;

        if (arvalid) begin
            rdata  <= reg0;
            rvalid <= 1'b1;
        end

        if (rready)
            rvalid <= 1'b0;
    end
end

assign awready = 1'b1;
assign wready  = 1'b1;
assign arready = 1'b1;

endmodule
