module uart_tx #(
    parameter CLKS_PER_BIT = 87
)(
    input  logic       clk,
    input  logic       rst,
    input  logic       tx_start,
    input  logic [7:0] tx_data,
    output logic       tx_serial,
    output logic       tx_busy
);

typedef enum logic [2:0] {
    IDLE,
    START,
    DATA,
    STOP
} state_t;

state_t state;
logic [7:0] data_reg;
logic [3:0] bit_index;
logic [15:0] clk_count;

always_ff @(posedge clk) begin
    if (rst) begin
        state      <= IDLE;
        tx_serial  <= 1'b1;
        tx_busy    <= 1'b0;
        clk_count  <= 0;
        bit_index  <= 0;
    end
    else begin
        case (state)

        IDLE: begin
            tx_serial <= 1'b1;
            tx_busy   <= 1'b0;
            if (tx_start) begin
                data_reg <= tx_data;
                state    <= START;
                tx_busy  <= 1'b1;
            end
        end

        START: begin
            tx_serial <= 1'b0;
            if (clk_count == CLKS_PER_BIT-1) begin
                clk_count <= 0;
                state     <= DATA;
            end
            else
                clk_count <= clk_count + 1;
        end

        DATA: begin
            tx_serial <= data_reg[bit_index];
            if (clk_count == CLKS_PER_BIT-1) begin
                clk_count <= 0;
                if (bit_index == 7)
                    state <= STOP;
                else
                    bit_index <= bit_index + 1;
            end
            else
                clk_count <= clk_count + 1;
        end

        STOP: begin
            tx_serial <= 1'b1;
            if (clk_count == CLKS_PER_BIT-1) begin
                state <= IDLE;
            end
            else
                clk_count <= clk_count + 1;
        end

        endcase
    end
end

endmodule
